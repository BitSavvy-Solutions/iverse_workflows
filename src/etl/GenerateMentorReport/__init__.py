"""
src/etl/GenerateMentorReport/__init__.py
─────────────────────────────────────────
Daily mentor report — reads slackUpdates from Cosmos DB and sends
an HTML email summary to the team.

Triggers:
  - Timer: every day at 18:00 UTC automatically
  - HTTP POST: for manual testing (no body required)

To test manually:
  POST https://iverse-workflows.azurewebsites.net/api/GenerateMentorReport
"""

import logging
import json
import os
from datetime import datetime, timezone, timedelta

import requests
import azure.functions as func
from pymongo import MongoClient

from shared.email_service import send_email_to_list

# ── Slack user name resolver ──────────────────────────────────────────────────

_user_name_cache: dict[str, str] = {}

def _resolve_user_name(slack_user_id: str) -> str:
    """Resolves Slack user ID to real name. Caches results within one run."""
    if slack_user_id in _user_name_cache:
        return _user_name_cache[slack_user_id]

    token = os.environ.get("SLACK_BOT_TOKEN")
    if not token:
        return slack_user_id

    try:
        resp = requests.get(
            "https://slack.com/api/users.info",
            headers={"Authorization": f"Bearer {token}"},
            params={"user": slack_user_id},
            timeout=10,
        )
        data = resp.json()
        if data.get("ok"):
            profile = data["user"].get("profile", {})
            name = profile.get("real_name") or profile.get("display_name") or slack_user_id
        else:
            name = slack_user_id
    except Exception as e:
        logging.warning(f"Could not resolve user {slack_user_id}: {e}")
        name = slack_user_id

    _user_name_cache[slack_user_id] = name
    return name

# ── Config ────────────────────────────────────────────────────────────────────

DB_NAME         = "coursedb"
COLLECTION_NAME = "slackUpdates"

# Days without any update before a student is flagged as potentially stuck
SILENT_THRESHOLD_DAYS = 5

# Test recipients — team emails while we're in testing mode
TEST_RECIPIENTS = [
    "yuliiakuts@gmail.com",
    "mayank.kr@pm.me",
    
]

# TODO: KNOWN_STUDENTS removed — roster now loaded from coursedb.cohortStudents
# Migrate to userdb.users.slackUserId when all students register on platform.


# ── Entry points ──────────────────────────────────────────────────────────────

def main(req: func.HttpRequest) -> func.HttpResponse:
    """Handles both HTTP (manual test) and Timer (scheduled) triggers."""
    logging.info("GenerateMentorReport triggered")

    try:
        report_data = _build_report()
        _send_email(report_data)

        return func.HttpResponse(
            json.dumps({
                "success":      True,
                "date":         report_data["date"],
                "postedCount":  report_data["postedCount"],
                "silentCount":  report_data["silentCount"],
                "stuckCount":   report_data["stuckCount"],
                "emailsSentTo": TEST_RECIPIENTS,
            }),
            status_code=200,
            mimetype="application/json",
        )

    except Exception as e:
        logging.error(f"GenerateMentorReport failed: {e}", exc_info=True)
        return func.HttpResponse(
            json.dumps({"success": False, "error": str(e)}),
            status_code=500,
            mimetype="application/json",
        )


# ── Report builder ────────────────────────────────────────────────────────────

def _build_report() -> dict:
    """Reads slackUpdates from Cosmos DB and assembles the report data."""
    now      = datetime.now(timezone.utc)
    date_str = now.strftime("%Y-%m-%d")

    client     = MongoClient(os.environ["COSMOS_CONNECTION_STRING"])
    collection = client[DB_NAME][COLLECTION_NAME]

    # ── Load cohort roster from DB ────────────────────────────────────────────
    # Source: coursedb.cohortStudents (temporary — see load_cohort_students.py)
    # TODO: Replace with userdb.users query when all students register on platform
    cohort_col = client["coursedb"]["cohortStudents"]
    cohort_students = list(cohort_col.find({"courseId": "fullstack-2025"}))

    # Build lookup maps
    # slackUserId → official name
    id_to_name  = {s["slackUserId"]: s["name"] for s in cohort_students if s.get("slackUserId")}
    # official name → slackUserId (for stuck check)
    name_to_id  = {s["name"]: s.get("slackUserId") for s in cohort_students}
    all_names   = [s["name"] for s in cohort_students]

    logging.info(f"Loaded {len(cohort_students)} students from cohortStudents ({len(id_to_name)} with Slack ID)")

    # ── Who posted today ──────────────────────────────────────────────────────
    todays_updates = list(collection.find({"dateStr": date_str}))
    logging.info(f"Found {len(todays_updates)} updates for {date_str}")

    # Key by official name (from cohortStudents) or Slack display name as fallback
    posted_today: dict[str, list] = {}
    for update in todays_updates:
        slack_id = update.get("slackUserId", "")
        # Try official name from cohort roster first
        name = id_to_name.get(slack_id) or update.get("slackUserName") or _resolve_user_name(slack_id)
        posted_today.setdefault(name, []).append(update)

    # ── Who did NOT post today ────────────────────────────────────────────────
    silent_today = sorted([n for n in all_names if n not in posted_today])

    # ── Who is silent 5+ days ─────────────────────────────────────────────────
    cutoff_str = (now - timedelta(days=SILENT_THRESHOLD_DAYS)).strftime("%Y-%m-%d")

    stuck_students = []
    for name in all_names:
        slack_id = name_to_id.get(name)

        # Find last update by slackUserId if available, else by name
        query = {"slackUserId": slack_id} if slack_id else {"slackUserName": name}
        all_updates = list(collection.find(query, {"savedAt": 1, "dateStr": 1}))
        last = max(all_updates, key=lambda u: u.get("savedAt", ""), default=None) if all_updates else None

        if not last:
            stuck_students.append({"name": name, "lastSeen": "never", "daysSilent": "∞"})
        elif last.get("dateStr", "") < cutoff_str:
            days = (now.date() - datetime.strptime(last["dateStr"], "%Y-%m-%d").date()).days
            stuck_students.append({"name": name, "lastSeen": last["dateStr"], "daysSilent": days})

    client.close()

    # ── Per-student summary ───────────────────────────────────────────────────
    student_summaries = []
    for name, updates in posted_today.items():
        latest = sorted(updates, key=lambda u: u.get("messageTs", ""), reverse=True)[0]
        match  = latest.get("curriculumMatch")
        student_summaries.append({
            "name":          name,
            "chapter":       match["chapterTitle"] if match else "—",
            "chapterId":     match.get("chapterId") if match else None,
            "confidence":    match.get("confidence", "") if match else "",
            "matchMethod":   match.get("matchMethod", "") if match else "",
            "messageSnippet": latest.get("messageText", "")[:120],
        })
    student_summaries.sort(key=lambda x: x["name"])

    return {
        "date":             date_str,
        "totalKnown":       len(all_names),
        "postedCount":      len(posted_today),
        "silentCount":      len(silent_today),
        "stuckCount":       len(stuck_students),
        "studentSummaries": student_summaries,
        "silentToday":      silent_today,
        "stuckStudents":    stuck_students,
    }


# ── Email sender ──────────────────────────────────────────────────────────────

def _send_email(data: dict) -> None:
    subject = (
        f"📚 Mentor Report — {data['date']} "
        f"({data['postedCount']}/{data['totalKnown']} active)"
    )
    html = _render_html(data)
    text = _render_text(data)

    sent, errors = send_email_to_list(TEST_RECIPIENTS, subject, html, text)
    logging.info(f"Email sent: {sent} ok, {errors} errors")


# ── HTML template ─────────────────────────────────────────────────────────────

def _render_html(data: dict) -> str:
    # Student rows
    student_rows = ""
    for s in data["studentSummaries"]:
        chapter_color = "#3fb950" if s["chapterId"] else "#8b949e"
        badge = ""
        if s["confidence"]:
            color = {"high": "#3fb950", "medium": "#f0c040", "low": "#ff7b72"}.get(s["confidence"], "#8b949e")
            badge = (
                f'<span style="background:{color};color:#000;font-size:9px;'
                f'padding:2px 5px;border-radius:3px;margin-left:6px;">'
                f'{s["confidence"]}</span>'
            )
        method = f'<span style="color:#8b949e;font-size:10px;margin-left:4px;">({s["matchMethod"]})</span>' if s["matchMethod"] else ""
        student_rows += f"""
        <tr style="border-bottom:1px solid #21262d;">
          <td style="padding:9px 12px;color:#e6edf3;font-size:13px;">{s['name']}</td>
          <td style="padding:9px 12px;font-size:12px;white-space:nowrap;">
            <span style="color:{chapter_color};">{s['chapter']}</span>{badge}{method}
          </td>
          <td style="padding:9px 12px;color:#8b949e;font-size:11px;">{s['messageSnippet']}…</td>
        </tr>"""

    # Silent list
    silent_items = "".join(
        f'<li style="color:#f0c040;margin:5px 0;font-size:13px;">{n}</li>'
        for n in data["silentToday"]
    ) or '<li style="color:#8b949e;">Everyone posted today 🎉</li>'

    # Stuck list
    stuck_items = "".join(
        f'<li style="color:#ff7b72;margin:5px 0;font-size:13px;">'
        f'{s["name"]} — last seen: <strong>{s["lastSeen"]}</strong> ({s["daysSilent"]} days ago)</li>'
        for s in data["stuckStudents"]
    ) or '<li style="color:#8b949e;">No students flagged 🎉</li>'

    return f"""<!DOCTYPE html>
<html>
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0"></head>
<body style="margin:0;padding:0;background:#0d1117;font-family:'Helvetica Neue',Helvetica,Arial,sans-serif;">
<table width="100%" cellpadding="0" cellspacing="0" style="background:#0d1117;padding:32px 0;">
<tr><td align="center">
<table width="660" cellpadding="0" cellspacing="0"
  style="background:#161b22;border-radius:12px;border:1px solid #30363d;max-width:100%;">

  <!-- Header -->
  <tr><td style="background:#FF5F90;padding:28px 32px;border-radius:12px 12px 0 0;text-align:center;">
    <h1 style="margin:0;color:#fff;font-size:22px;font-weight:700;">📚 Daily Mentor Report</h1>
    <p style="margin:6px 0 0;color:rgba(255,255,255,0.85);font-size:14px;">
      {data['date']} · CodeBlossom Full Stack Cohort · <em>Test mode</em>
    </p>
  </td></tr>

  <!-- Stats -->
  <tr><td style="padding:24px 32px 16px;">
    <table width="100%" cellpadding="0" cellspacing="0"><tr>
      <td width="23%" style="background:#21262d;border-radius:8px;padding:16px;text-align:center;">
        <div style="font-size:10px;color:#8b949e;text-transform:uppercase;letter-spacing:1px;">Total</div>
        <div style="font-size:30px;font-weight:700;color:#e6edf3;margin-top:4px;">{data['totalKnown']}</div>
      </td><td width="2%"></td>
      <td width="23%" style="background:#21262d;border-radius:8px;padding:16px;text-align:center;">
        <div style="font-size:10px;color:#3fb950;text-transform:uppercase;letter-spacing:1px;">Posted</div>
        <div style="font-size:30px;font-weight:700;color:#3fb950;margin-top:4px;">{data['postedCount']}</div>
      </td><td width="2%"></td>
      <td width="23%" style="background:#21262d;border-radius:8px;padding:16px;text-align:center;">
        <div style="font-size:10px;color:#f0c040;text-transform:uppercase;letter-spacing:1px;">Silent today</div>
        <div style="font-size:30px;font-weight:700;color:#f0c040;margin-top:4px;">{data['silentCount']}</div>
      </td><td width="2%"></td>
      <td width="23%" style="background:#21262d;border-radius:8px;padding:16px;text-align:center;">
        <div style="font-size:10px;color:#ff7b72;text-transform:uppercase;letter-spacing:1px;">Stuck 5d+</div>
        <div style="font-size:30px;font-weight:700;color:#ff7b72;margin-top:4px;">{data['stuckCount']}</div>
      </td>
    </tr></table>
  </td></tr>

  <!-- Posted today table -->
  <tr><td style="padding:8px 32px 24px;">
    <h2 style="color:#e6edf3;font-size:15px;margin:0 0 12px;">✅ Posted today</h2>
    <table width="100%" cellpadding="0" cellspacing="0"
      style="background:#21262d;border-radius:8px;border:1px solid #30363d;">
      <tr style="background:#161b22;border-radius:8px 8px 0 0;">
        <th style="padding:8px 12px;color:#8b949e;font-size:11px;text-align:left;text-transform:uppercase;width:28%;">Student</th>
        <th style="padding:8px 12px;color:#8b949e;font-size:11px;text-align:left;text-transform:uppercase;width:22%;">Chapter</th>
        <th style="padding:8px 12px;color:#8b949e;font-size:11px;text-align:left;text-transform:uppercase;">Update snippet</th>
      </tr>
      {student_rows or '<tr><td colspan="3" style="padding:16px;color:#8b949e;text-align:center;font-size:13px;">No updates yet today</td></tr>'}
    </table>
  </td></tr>

  <!-- Silent today -->
  <tr><td style="padding:0 32px 24px;">
    <h2 style="color:#e6edf3;font-size:15px;margin:0 0 12px;">⚠️ No update today</h2>
    <div style="background:#21262d;border-radius:8px;padding:16px 20px;">
      <ul style="margin:0;padding-left:18px;">{silent_items}</ul>
    </div>
  </td></tr>

  <!-- Stuck 5d+ -->
  <tr><td style="padding:0 32px 32px;">
    <h2 style="color:#e6edf3;font-size:15px;margin:0 0 12px;">🚨 Silent 5+ days — check in!</h2>
    <div style="background:#21262d;border-radius:8px;padding:16px 20px;">
      <ul style="margin:0;padding-left:18px;">{stuck_items}</ul>
    </div>
  </td></tr>

  <!-- Footer -->
  <tr><td style="padding:16px 32px;border-top:1px solid #21262d;text-align:center;">
    <p style="color:#6b7280;font-size:11px;margin:0;">
      Generated automatically by AITut.Iverse · {data['date']}
    </p>
  </td></tr>

</table>
</td></tr>
</table>
</body>
</html>"""


def _render_text(data: dict) -> str:
    lines = [
        f"Daily Mentor Report — {data['date']}",
        "=" * 50,
        f"Total: {data['totalKnown']}  Posted: {data['postedCount']}  "
        f"Silent: {data['silentCount']}  Stuck 5d+: {data['stuckCount']}",
        "", "POSTED TODAY:",
    ]
    for s in data["studentSummaries"]:
        lines.append(f"  {s['name']} — {s['chapter']}")
    lines += ["", "NO UPDATE TODAY:"]
    for n in data["silentToday"]:
        lines.append(f"  {n}")
    lines += ["", "SILENT 5+ DAYS:"]
    for s in data["stuckStudents"]:
        lines.append(f"  {s['name']} (last seen: {s['lastSeen']}, {s['daysSilent']} days)")
    return "\n".join(lines)