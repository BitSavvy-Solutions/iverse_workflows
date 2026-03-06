"""
src/etl/shared/curriculum_search.py
────────────────────────────────────
Hybrid approach to match a Slack standup message to a curriculum chapter:

  Step 1 — Keyword matching (instant, free)
            Catches ~80% of messages where students mention
            specific technologies (SQL, React, GitHub, etc.)

  Step 2 — Vector search (OpenRouter embeddings + cosine similarity)
            Only runs if keyword matching found nothing.
            Runs only on the "what I did" section of the message,
            not on "tomorrow" / "blockers" noise.

  Step 3 — Return null
            If neither step found a confident match
            (e.g. "Attended mentor session", "Nothing blocking me")

Usage:
    from shared.curriculum_search import get_chapter_for_standup

    result = get_chapter_for_standup("Learned SQL joins and aggregate functions")
    # → {"chapterId": "ch08", "chapterTitle": "Databases",
    #    "confidence": "high", "matchMethod": "keyword"}
"""

import os
import re
import math
import logging
import requests
from pymongo import MongoClient
from typing import Optional

EMBEDDING_MODEL = "openai/text-embedding-3-small"
DB_NAME         = "coursedb"
COLLECTION_NAME = "curriculumEmbeddings"


# ── Step 1: Keyword map ───────────────────────────────────────────────────────
#
# Each entry: (chapter_id, chapter_title, [keywords...])
# Keywords are matched case-insensitively against the full message text.
# Order matters — more specific entries should come first.
# If a message matches multiple chapters, the first match wins.

KEYWORD_MAP = [
    # ch10 — Node.js & Express (check before generic "js" keywords)
    (
        "ch10",
        "Node.js and Express",
        [
            "node.js", "nodejs", "node js", "express", "express.js",
            "backend", "back-end", "back end", "rest api", "restapi",
            "api endpoint", "server side", "server-side", "middleware",
            "http server", "postman", "npm install",
        ],
    ),
    # ch09 — React (check before generic "js" keywords)
    (
        "ch09",
        "React Fundamentals",
        [
            "react", "jsx", "usestate", "useeffect",
            "hooks", "react dom", "reactdom", "vite",
            "react router", "redux",
        ],
    ),
    # ch08 — Databases
    (
        "ch08",
        "Databases",
        [
            "sql", "mysql", "postgresql", "sqlite", "database",
            "mongodb", "mongo db", "mongoose", "nosql", "no-sql",
            "select ", "insert into", "inner join", "outer join",
            "sql server", "ssms", "aggregate function",
            "collection", "document store",
        ],
    ),
    # ch07 — TypeScript
    (
        "ch07",
        "TypeScript",
        [
            "typescript", ".ts", "type annotation",
            "interface", "generics", "enum", "tsc",
        ],
    ),
    # ch06 — Building Dynamic Websites (Bootstrap)
    (
        "ch06",
        "Building Dynamic Websites",
        [
            "bootstrap", "navbar", "responsive design", "grid system",
            "carousel", "modal", "tailwind",
        ],
    ),
    # ch05 — Portfolio Assignment
    (
        "ch05",
        "Portfolio Assignment",
        [
            "portfolio", "personal website",
            "portfolio project", "dark theme", "light theme",
            "about page", "contact page", "skills page",
        ],
    ),
    # ch04 — Version Control & Hosting
    (
        "ch04",
        "Version Control and Hosting",
        [
            "github", "gitlab", "version control",
            "pull request", "merge conflict",
            "netlify", "github pages",
        ],
    ),
    # ch03 — HTML & CSS
    (
        "ch03",
        "HTML and CSS",
        [
            "html", "css", "stylesheet", "boilerplate",
            "box model", "media query",
            "selector", "<div>", "<span>",
        ],
    ),
    # ch02 — JavaScript
    (
        "ch02",
        "Interactivity and User Experience",
        [
            "javascript", "es6", "es2015", "dom manipulation",
            "event listener", "callback", "promise",
            "async await", "freecodecamp",
            "algorithm", "data structure", "regex", "regular expression",
            "debugging",
        ],
    ),
    # ch01 — How the Internet Works
    (
        "ch01",
        "How the Internet Works",
        [
            "http", "https", "dns", "tcp/ip",
            "web server", "client server", "protocol",
            "how the internet",
        ],
    ),
]


# ── Step 1 implementation ─────────────────────────────────────────────────────

def _keyword_match(text: str) -> Optional[dict]:
    """
    Scans message text for known technology keywords.
    Returns chapter info on first match, or None.
    """
    text_lower = text.lower()

    for chapter_id, chapter_title, keywords in KEYWORD_MAP:
        for kw in keywords:
            if kw.lower() in text_lower:
                logging.info(
                    f"Keyword match: '{kw}' → {chapter_id} ({chapter_title})"
                )
                return {
                    "chapterId":      chapter_id,
                    "chapterTitle":   chapter_title,
                    "matchedKeyword": kw,
                }

    return None


# ── Step 2a: Extract "what I did" section ─────────────────────────────────────

# Patterns marking start of "tomorrow" / "blockers" sections — stop reading here
_NOISE_PATTERNS = re.compile(
    r"(what\s+(will|i'?ll?|i\s+will|i\s+plan)\s+i\s+do"
    r"|what'?s?\s+next"
    r"|tomorrow'?s?\s+plan"
    r"|anything\s+block"
    r"|is\s+there\s+.{0,20}block"
    r"|what\s+is\s+(stopping|holding|blocking)"
    r"|blockers?"
    r")",
    re.IGNORECASE,
)

def _extract_did_section(text: str) -> str:
    """
    Returns only the 'what I did' portion of a standup message,
    discarding 'tomorrow' and 'blockers' sections to reduce noise.

    Example:
        Input:  "Learned React hooks today.\\nTomorrow: Redux.\\nBlockers: none."
        Output: "Learned React hooks today."
    """
    lines = text.splitlines()
    cleaned = []

    for line in lines:
        if _NOISE_PATTERNS.search(line):
            break  # Stop at first "tomorrow" / "blockers" heading
        cleaned.append(line)

    result = "\n".join(cleaned).strip()

    # If nothing survived (e.g. very short message), fall back to full text
    return result if len(result) > 20 else text


# ── Step 2b: Cosine similarity ────────────────────────────────────────────────

def _cosine_similarity(vec_a: list[float], vec_b: list[float]) -> float:
    """Cosine similarity between two float vectors (-1.0 to 1.0)."""
    dot   = sum(a * b for a, b in zip(vec_a, vec_b))
    mag_a = math.sqrt(sum(a * a for a in vec_a))
    mag_b = math.sqrt(sum(b * b for b in vec_b))
    if mag_a == 0 or mag_b == 0:
        return 0.0
    return dot / (mag_a * mag_b)


def _get_embedding(text: str) -> list[float]:
    """Calls OpenRouter to generate a text embedding for the given text."""
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


def _vector_match(text: str, course_id: str) -> Optional[dict]:
    """
    Generates an embedding for `text` and finds the closest curriculum material.
    Returns chapter info if best score >= 0.48, else None.
    """
    query_embedding = _get_embedding(text)

    client     = MongoClient(os.environ["COSMOS_CONNECTION_STRING"])
    collection = client[DB_NAME][COLLECTION_NAME]

    materials = list(collection.find(
        {"courseId": course_id},
        {"chapterId": 1, "chapterTitle": 1, "materialId": 1,
         "materialTitle": 1, "materialType": 1, "embedding": 1},
    ))
    client.close()

    if not materials:
        logging.warning(f"No embeddings found for courseId='{course_id}'")
        return None

    # Score every material against the query
    scored = []
    for mat in materials:
        if not mat.get("embedding"):
            continue
        score = _cosine_similarity(query_embedding, mat["embedding"])
        scored.append((score, mat))

    scored.sort(key=lambda x: x[0], reverse=True)

    # Log top 3 scores for debugging
    for i, (score, mat) in enumerate(scored[:3]):
        logging.info(
            f"  Vector top {i+1}: {mat['chapterTitle']} / "
            f"{mat['materialTitle']} (score={score:.4f})"
        )

    best_score, best_mat = scored[0]

    # Threshold tuned for text-embedding-3-small conservative score range
    if best_score < 0.48:
        logging.info(f"Vector: no confident match (best={best_score:.4f})")
        return None

    return {
        "chapterId":     best_mat["chapterId"],
        "chapterTitle":  best_mat["chapterTitle"],
        "materialId":    best_mat["materialId"],
        "materialTitle": best_mat["materialTitle"],
        "score":         round(best_score, 4),
    }


# ── Public API ────────────────────────────────────────────────────────────────

def get_chapter_for_standup(
    standup_text: str,
    course_id: str = "fullstack-2025",
) -> Optional[dict]:
    """
    Main entry point. Returns the best curriculum chapter match for a standup.

    Strategy:
        1. Keyword match on full text       → fast, free, ~80% coverage
        2. Vector match on "did" section    → handles edge cases
        3. null                             → non-study message

    Returns:
        {
            "chapterId":    "ch08",
            "chapterTitle": "Databases",
            "confidence":   "high" | "medium" | "low",
            "matchMethod":  "keyword" | "vector",

            # keyword match only:
            "matchedKeyword": "mongodb",

            # vector match only:
            "materialId":    "mat08-02",
            "materialTitle": "Introduction to MongoDB",
            "score":         0.54,
        }
        or None if no match found.
    """

    # ── Step 1: Keyword matching ──────────────────────────────────────────────
    kw_result = _keyword_match(standup_text)

    if kw_result:
        return {
            "chapterId":      kw_result["chapterId"],
            "chapterTitle":   kw_result["chapterTitle"],
            "confidence":     "high",
            "matchMethod":    "keyword",
            "matchedKeyword": kw_result["matchedKeyword"],
        }

    # ── Step 2: Vector search on "what I did" section only ───────────────────
    logging.info("No keyword match — falling back to vector search")

    did_section = _extract_did_section(standup_text)
    logging.info(
        f"'Did' section extracted ({len(did_section)} chars): "
        f"{did_section[:120]}..."
    )

    try:
        vec_result = _vector_match(did_section, course_id)
    except Exception as e:
        logging.error(f"Vector search failed: {e}")
        return None

    if vec_result:
        score = vec_result["score"]
        confidence = "high" if score >= 0.55 else ("medium" if score >= 0.50 else "low")
        return {
            "chapterId":     vec_result["chapterId"],
            "chapterTitle":  vec_result["chapterTitle"],
            "materialId":    vec_result["materialId"],
            "materialTitle": vec_result["materialTitle"],
            "confidence":    confidence,
            "matchMethod":   "vector",
            "score":         score,
        }

    # ── Step 3: No match ──────────────────────────────────────────────────────
    logging.info("No curriculum match found (likely non-study message)")
    return None