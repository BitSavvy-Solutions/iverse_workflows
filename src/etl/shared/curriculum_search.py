"""
src/etl/shared/curriculum_search.py
────────────────────────────────────
Finds the most relevant curriculum chapter/material for any free text
(standup message, question, topic) using cosine similarity on stored embeddings.

No native vector index needed — works with regular Cosmos DB (RU-based Serverless).

Import anywhere in the Azure Functions project:
    from shared.curriculum_search import get_chapter_for_standup
"""

import os
import math
import logging
import requests
from pymongo import MongoClient
from typing import Optional

EMBEDDING_MODEL = "openai/text-embedding-3-small"
DB_NAME         = "coursedb"
COLLECTION_NAME = "curriculumEmbeddings"


# ── Cosine similarity (pure Python, no extra dependencies) ────────────────────

def cosine_similarity(vec_a: list[float], vec_b: list[float]) -> float:
    """
    Returns cosine similarity between two vectors (float, -1.0 to 1.0).
    For text embeddings: 0.82+ = strong match, 0.70+ = good, below 0.65 = weak.
    """
    dot   = sum(a * b for a, b in zip(vec_a, vec_b))
    mag_a = math.sqrt(sum(a * a for a in vec_a))
    mag_b = math.sqrt(sum(b * b for b in vec_b))
    if mag_a == 0 or mag_b == 0:
        return 0.0
    return dot / (mag_a * mag_b)


# ── Embedding helper ──────────────────────────────────────────────────────────

def _get_embedding(text: str) -> list[float]:
    """Calls OpenRouter to embed a piece of text."""
    response = requests.post(
        "https://openrouter.ai/api/v1/embeddings",
        headers={
            "Authorization": f"Bearer {os.environ['OPENROUTER_API_KEY']}",
            "HTTP-Referer":  "https://aitut.iverse.com",
            "X-Title":       "AITut.Iverse Curriculum Search",
        },
        json={"model": EMBEDDING_MODEL, "input": text[:8000]},
        timeout=30,
    )
    response.raise_for_status()
    return response.json()["data"][0]["embedding"]


# ── Main search ───────────────────────────────────────────────────────────────

def find_curriculum_matches(
    text: str,
    course_id: str = "fullstack-2025",
    top_k: int = 3,
    min_score: float = 0.65,
) -> list[dict]:
    """
    Returns top-k curriculum materials most semantically similar to `text`.

    Args:
        text:      Any free text — standup message, student question, etc.
        course_id: Curriculum to search within (supports multiple curricula).
        top_k:     Max number of results.
        min_score: Minimum cosine similarity (0.0–1.0). Below this = ignored.

    Returns list of dicts sorted by score descending:
        [{"score": 0.89, "chapterId": "ch02", "chapterTitle": "...",
          "materialId": "mat02-02", "materialTitle": "...", "materialType": "course"}, ...]
    """
    query_embedding = _get_embedding(text)

    client     = MongoClient(os.environ["COSMOS_CONNECTION_STRING"])
    collection = client[DB_NAME][COLLECTION_NAME]

    # Fetch all embeddings for this course
    # For 30–50 materials this is instant; cache in memory if needed at scale
    materials = list(collection.find(
        {"courseId": course_id},
        {"chapterId": 1, "chapterTitle": 1, "chapterOrder": 1,
         "materialId": 1, "materialTitle": 1, "materialType": 1,
         "embedding": 1}
    ))
    client.close()

    if not materials:
        logging.warning(f"No embeddings found for courseId='{course_id}'")
        return []

    # Score every material
    scored = []
    for mat in materials:
        if not mat.get("embedding"):
            continue
        score = cosine_similarity(query_embedding, mat["embedding"])
        scored.append({
            "score":        round(score, 4),
            "chapterId":    mat["chapterId"],
            "chapterTitle": mat["chapterTitle"],
            "chapterOrder": mat.get("chapterOrder", 0),
            "materialId":   mat["materialId"],
            "materialTitle": mat["materialTitle"],
            "materialType": mat.get("materialType", ""),
        })

    # Sort, filter, cap
    results = sorted(scored, key=lambda x: x["score"], reverse=True)

    # Log top 3 scores to help debug threshold tuning
    for i, r in enumerate(results[:3]):
        logging.info(
            f"  Top {i+1}: {r['chapterTitle']} / {r['materialTitle']} "
            f"(score={r['score']})"
        )

    results = [r for r in results if r["score"] >= min_score][:top_k]

    if results:
        best = results[0]
        logging.info(
            f"Vector match: '{text[:60]}...' → "
            f"{best['chapterTitle']} / {best['materialTitle']} "
            f"(score={best['score']})"
        )

    return results


# ── Convenience wrapper used by n8n / AIDA pipeline ──────────────────────────

def get_chapter_for_standup(
    standup_text: str,
    course_id: str = "fullstack-2025",
) -> Optional[dict]:
    """
    Simplified function: takes a standup message, returns the single best
    chapter match with a human-readable confidence label.

    Used in SaveSlackStandup Azure Function and n8n workflow.

    Returns:
        {
          "chapterId":    "ch02",
          "chapterTitle": "Interactivity and User Experience",
          "materialId":   "mat02-02",
          "materialTitle": "JavaScript Algorithms and Data Structures",
          "confidence":   "high" | "medium" | "low",
          "score":        0.89
        }
        or None if no match above threshold.
    """
    matches = find_curriculum_matches(
        text=standup_text,
        course_id=course_id,
        top_k=1,
        min_score=0.60,
    )

    if not matches:
        return None

    best  = matches[0]
    score = best["score"]

    confidence = "high" if score >= 0.82 else ("medium" if score >= 0.70 else "low")

    return {
        "chapterId":     best["chapterId"],
        "chapterTitle":  best["chapterTitle"],
        "materialId":    best["materialId"],
        "materialTitle": best["materialTitle"],
        "confidence":    confidence,
        "score":         score,
    }
