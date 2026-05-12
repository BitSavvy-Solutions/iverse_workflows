"""
src/etl/SaveSlackUpdate/__init__.py
────────────────────────────────────
Receives a raw Slack standup message from n8n,
finds the matching curriculum chapter via vector search,
and saves everything to Cosmos DB coursedb.slackUpdates.

n8n does nothing smart — just passes the raw message here.
All logic lives in this function.

Endpoint: POST /api/SaveSlackUpdate
Body: {
    "slackUserId":   "U09100PMZED",
    "slackUserName": "Magdalena",
    "messageTs":     "1738234567.123456",
    "messageText":   "Continued learning MongoDB collections and documents...",
    "channelName":   "daily-updates-fullstack",   // optional
    "studentEmail":  "magdalena@email.com"         // optional, if n8n resolves it
}
"""

import logging
import json
import os
from datetime import datetime, timezone

import azure.functions as func
from pymongo import MongoClient, ASCENDING

from shared.curriculum_search import get_chapter_for_standup

DB_NAME         = "coursedb"
COLLECTION_NAME = "slackUpdates"


def main(req: func.HttpRequest) -> func.HttpResponse:
    logging.info("SaveSlackUpdate triggered")

    # ── 1. Parse request ──────────────────────────────────────────────────────
    try:
        body = req.get_json()
    except ValueError:
        return _error("Invalid JSON body", 400)

    slack_user_id   = body.get("slackUserId")
    message_ts      = body.get("messageTs")
    message_text    = body.get("messageText")

    if not slack_user_id or not message_ts or not message_text:
        return _error("Missing required fields: slackUserId, messageTs, messageText", 400)

    # Optional metadata passed by n8n
    slack_user_name = body.get("slackUserName", "")
    channel_name    = body.get("channelName", "")
    student_email   = body.get("studentEmail", "")
    course_id       = body.get("courseId", "fullstack-2025")

    # ── 2. Connect to Cosmos DB ───────────────────────────────────────────────
    client     = MongoClient(os.environ["COSMOS_CONNECTION_STRING"])
    collection = client[DB_NAME][COLLECTION_NAME]
    _ensure_indexes(collection)

    # ── 3. Deduplication ─────────────────────────────────────────────────────
    # Same message can arrive twice if n8n retries — skip silently
    existing = collection.find_one({
        "slackUserId": slack_user_id,
        "messageTs":   message_ts,
    })
    if existing:
        client.close()
        logging.info(f"Duplicate skipped: {slack_user_id} / {message_ts}")
        return func.HttpResponse(
            json.dumps({"success": True, "duplicate": True}),
            status_code=200,
            mimetype="application/json",
        )

    # ── 4. Vector search — find curriculum chapter ────────────────────────────
    curriculum_match = None
    try:
        curriculum_match = get_chapter_for_standup(
            standup_text=message_text,
            course_id=course_id,
        )
        if curriculum_match:
            logging.info(
                f"Match: {curriculum_match['chapterTitle']} "
                f"via {curriculum_match.get('matchMethod','?')} "
                f"(confidence={curriculum_match.get('confidence','?')}, "
                f"score={curriculum_match.get('score','n/a')})"
            )
        else:
            # Happens for messages about events, meetings etc — that's fine
            logging.info("No curriculum match found (likely non-study message)")
    except Exception as e:
        # Don't block the save if vector search fails
        logging.error(f"Vector search failed (non-fatal): {e}")

    # ── 5. Save to Cosmos DB ──────────────────────────────────────────────────
    now = datetime.now(timezone.utc)

    doc = {
        # Slack identifiers
        "slackUserId":   slack_user_id,
        "slackUserName": slack_user_name,
        "studentEmail":  student_email,
        "messageTs":     message_ts,
        "channelName":   channel_name,

        # Raw message text — kept for future AIDA parsing if needed
        "messageText": message_text,

        # Curriculum match result (null if no confident match)
        "curriculumMatch": curriculum_match,

        # Course context
        "courseId": course_id,

        # Timestamps
        "savedAt": now,
        "dateStr": now.strftime("%Y-%m-%d"),  # for easy daily queries
    }

    collection.insert_one(doc)

    # Look up official student name from cohort roster
    cohort_entry  = client[DB_NAME]["cohortStudents"].find_one(
        {"slackUserId": slack_user_id}
    )
    # None means student is not in our cohort — Google Sheets node will skip them
    official_name = cohort_entry["name"] if cohort_entry else None

    client.close()

    chapter    = curriculum_match.get("chapterTitle") if curriculum_match else None
    chapter_id = curriculum_match.get("chapterId")    if curriculum_match else None

    logging.info(f"Saved: {official_name} ({slack_user_id})")

    # Return enriched response — n8n uses name + chapter for Google Sheets
    return func.HttpResponse(
        json.dumps({
            "success":       True,
            "duplicate":     False,
            "studentName":   official_name,  # None if not in cohort
            "inCohort":      official_name is not None,  # n8n uses this to filter
            "slackUserId":   slack_user_id,
            "chapter":       chapter,
            "chapterId":     chapter_id,
            "dateStr":       now.strftime("%Y-%m-%d"),
            "messageSnippet": message_text[:150],
        }),
        status_code=201,
        mimetype="application/json",
    )


# ── Index setup ───────────────────────────────────────────────────────────────

def _ensure_indexes(collection) -> None:
    """Creates indexes on slackUpdates collection if they don't exist yet."""

    # Deduplication — one document per Slack message
    collection.create_index(
        [("slackUserId", ASCENDING), ("messageTs", ASCENDING)],
        unique=True,
        name="slackUserId_messageTs_unique",
        background=True,
    )
    # Daily mentor dashboard queries: "all updates for today"
    collection.create_index(
        [("dateStr", ASCENDING), ("channelName", ASCENDING)],
        name="dateStr_channel",
        background=True,
    )
    # Filter by student email (links to users collection)
    collection.create_index(
        [("studentEmail", ASCENDING)],
        name="studentEmail",
        background=True,
    )


# ── Error helper ──────────────────────────────────────────────────────────────

def _error(message: str, status: int) -> func.HttpResponse:
    return func.HttpResponse(
        json.dumps({"success": False, "error": message}),
        status_code=status,
        mimetype="application/json",
    )
