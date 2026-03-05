"""
src/etl/IndexCurriculum/__init__.py
────────────────────────────────────
One-time (or on-demand) Azure Function that:
  1. Reads the full curriculum from GitHub (BitSavvy-Solutions/AITut-CB-FullStackCourse)
  2. Generates embeddings via OpenRouter (text-embedding-3-small)
  3. Stores them in Cosmos DB coursedb.curriculumEmbeddings

Trigger: HTTP POST (manual, run once per curriculum version)

Usage:
  POST /api/IndexCurriculum
  Body: {} or {"force_reindex": true}

  force_reindex=true  → delete all existing docs for the course and re-embed everything
  force_reindex=false → skip already-indexed materials (idempotent, safe to re-run)
"""

import logging
import json
import time
import os
import azure.functions as func
import httpx
from pymongo import MongoClient

# ── Constants ──────────────────────────────────────────────────────────────────

GITHUB_BASE = "https://raw.githubusercontent.com/BitSavvy-Solutions/AITut-CB-FullStackCourse/dev"
COURSE_ID   = "fullstack-2025"

# Chapter folder map — same as in github_service.py
CHAPTER_FOLDERS = {
    "ch01": "01-how-internet-works",
    "ch02": "02-interactivity-ux",
    "ch03": "03-html-and-css",
    "ch04": "04-version-control-and-hosting",
    "ch05": "05-paid-assignment-portfolio",
    "ch06": "06-building-dynamic-websites",
    "ch07": "07-typescript",
    "ch08": "08-databases",
    "ch09": "09-react",
    "ch10": "10-nodejs",
    "ch11": "11-paid-assignment-fullstack",
}

EMBEDDING_MODEL      = "openai/text-embedding-3-small"
EMBEDDING_DIMENSIONS = 1536
DB_NAME              = "coursedb"
COLLECTION_NAME      = "curriculumEmbeddings"


# ── GitHub helpers ─────────────────────────────────────────────────────────────

def fetch_json_from_github(path: str) -> dict | None:
    """Fetches a JSON file from the curriculum GitHub repo."""
    url = f"{GITHUB_BASE}/{path}"
    try:
        response = httpx.get(url, timeout=15)
        if response.status_code == 200:
            return response.json()
        logging.warning(f"GitHub 404: {url}")
        return None
    except Exception as e:
        logging.error(f"Error fetching {url}: {e}")
        return None


def fetch_markdown_from_github(path: str) -> str:
    """Fetches a Markdown file from the curriculum GitHub repo."""
    url = f"{GITHUB_BASE}/{path}"
    try:
        response = httpx.get(url, timeout=15)
        if response.status_code == 200:
            return response.text
        return ""
    except Exception as e:
        logging.error(f"Error fetching markdown {url}: {e}")
        return ""


# ── Embedding helper ───────────────────────────────────────────────────────────

def get_embedding(text: str) -> list[float]:
    """Calls OpenRouter to generate a text embedding."""
    api_key = os.environ["OPENROUTER_API_KEY"]
    response = httpx.post(
        "https://openrouter.ai/api/v1/embeddings",
        headers={
            "Authorization": f"Bearer {api_key}",
            "HTTP-Referer": "https://aitut.iverse.com",
            "X-Title": "AITut.Iverse Curriculum Indexer",
        },
        json={
            "model": EMBEDDING_MODEL,
            "input": text[:8000],
        },
        timeout=30,
    )
    response.raise_for_status()
    return response.json()["data"][0]["embedding"]


# ── Build text for embedding ───────────────────────────────────────────────────

def build_embedding_text(chapter: dict, material: dict, content: str) -> str:
    """
    Combines chapter context + material title + content snippet.
    The richer the text, the better the semantic matching.
    """
    objectives = " | ".join(chapter.get("objectives", []))
    content_snippet = content.strip()[:1500] if content else ""

    return (
        f"Chapter: {chapter['title']}\n"
        f"Objectives: {objectives}\n"
        f"Material: {material['title']} (type: {material['type']})\n"
        f"\n{content_snippet}"
    )


# ── Main function ──────────────────────────────────────────────────────────────

def main(req: func.HttpRequest) -> func.HttpResponse:
    logging.info("IndexCurriculum triggered")

    # Parse request
    try:
        body = req.get_json()
    except ValueError:
        body = {}
    force_reindex = body.get("force_reindex", False)

    # Connect to Cosmos DB
    client     = MongoClient(os.environ["COSMOS_CONNECTION_STRING"])
    collection = client[DB_NAME][COLLECTION_NAME]

    # Ensure dedup index
    collection.create_index(
        [("courseId", 1), ("materialId", 1)],
        unique=True,
        name="courseId_materialId_unique",
        background=True,
    )

    # Optionally wipe existing embeddings for this course
    if force_reindex:
        deleted = collection.delete_many({"courseId": COURSE_ID})
        logging.info(f"force_reindex: deleted {deleted.deleted_count} existing docs")

    # Fetch course.json from GitHub
    course = fetch_json_from_github("course.json")
    if not course:
        client.close()
        return func.HttpResponse(
            json.dumps({"success": False, "error": "Could not fetch course.json from GitHub"}),
            status_code=500, mimetype="application/json"
        )

    indexed = 0
    skipped = 0
    errors  = 0
    results = []

    for chapter_ref in course.get("chapters", []):
        chapter_id     = chapter_ref["chapterId"]
        folder_name    = CHAPTER_FOLDERS.get(chapter_id)

        if not folder_name:
            logging.warning(f"No folder mapping for {chapter_id}, skipping")
            continue

        # Fetch chapter.json
        chapter = fetch_json_from_github(f"chapters/{folder_name}/chapter.json")
        if not chapter:
            logging.warning(f"Could not fetch chapter.json for {chapter_id}")
            errors += 1
            continue

        logging.info(f"Processing {chapter_id}: {chapter['title']} ({len(chapter.get('materials', []))} materials)")

        for material in chapter.get("materials", []):
            material_id = material["materialId"]
            label       = f"{chapter_id}/{material_id}"

            # Skip if already indexed (unless force_reindex)
            if not force_reindex:
                existing = collection.find_one({
                    "courseId":   COURSE_ID,
                    "materialId": material_id,
                })
                if existing:
                    logging.info(f"  ⏭  Skip {label} (already indexed)")
                    skipped += 1
                    continue

            # Fetch markdown content
            material_path = f"chapters/{folder_name}/{material['path']}"
            content = fetch_markdown_from_github(material_path)

            # Build text and generate embedding
            text_to_embed = build_embedding_text(chapter, material, content)

            try:
                logging.info(f"  🔄 Embedding {label}...")
                embedding = get_embedding(text_to_embed)

                doc = {
                    "courseId":      COURSE_ID,
                    "courseTitle":   course.get("title", ""),
                    "chapterId":     chapter_id,
                    "chapterOrder":  chapter.get("order", 0),
                    "chapterTitle":  chapter["title"],
                    "materialId":    material_id,
                    "materialOrder": material.get("order", 0),
                    "materialTitle": material["title"],
                    "materialType":  material.get("type", ""),
                    "textForEmbedding": text_to_embed,
                    "embedding":     embedding,
                    "dimensions":    EMBEDDING_DIMENSIONS,
                    "modelUsed":     EMBEDDING_MODEL,
                    "indexedAt":     __import__("datetime").datetime.utcnow(),
                }

                collection.insert_one(doc)
                indexed += 1
                results.append({"materialId": material_id, "status": "indexed"})
                logging.info(f"  ✅ Stored {label}")

                # Small delay to stay within OpenRouter rate limits
                time.sleep(0.5)

            except Exception as e:
                logging.error(f"  ❌ Error on {label}: {e}")
                errors += 1
                results.append({"materialId": material_id, "status": "error", "error": str(e)})

    total_in_db = collection.count_documents({"courseId": COURSE_ID})
    client.close()

    summary = {
        "success":    True,
        "courseId":   COURSE_ID,
        "indexed":    indexed,
        "skipped":    skipped,
        "errors":     errors,
        "totalInDb":  total_in_db,
        "details":    results,
    }

    logging.info(f"IndexCurriculum complete: indexed={indexed}, skipped={skipped}, errors={errors}")

    return func.HttpResponse(
        json.dumps(summary, default=str),
        status_code=200,
        mimetype="application/json"
    )
