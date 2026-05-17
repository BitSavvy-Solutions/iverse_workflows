"""
src/etl/GenerateWeeklyReport/__init__.py
─────────────────────────────────────────
Weekly mentor report for Mentors (CodeBlossom).

Reads slackUpdates for the previous Mon-Sun week from Cosmos DB,
counts how many days each student posted, categorizes engagement,
and sends an HTML email to test recipients.

Trigger: HTTP POST (manual for now)
To run locally:
  curl -X POST http://localhost:7071/api/GenerateWeeklyReport

To specify a custom week:
  curl -X POST http://localhost:7071/api/GenerateWeeklyReport \
       -H "Content-Type: application/json" \
       -d '{"week_start": "2026-04-20"}'
"""

import logging
import json
import os
from datetime import datetime, timezone, timedelta

import requests
import azure.functions as func
from pymongo import MongoClient

from shared.email_service import send_email_to_list

# ── Config ────────────────────────────────────────────────────────────────────

DB_NAME     = "coursedb"
UPDATES_COL = "slackUpdates"
COHORT_COL  = "cohortStudents"
COURSE_ID   = "fullstack-2025"

TEST_RECIPIENTS = [
    "yuliiakuts@gmail.com",
    "mayank.kr@pm.me",
    "tamayodesh26@gmail.com",
    "anumghulam38@gmail.com",
    "sana.abbhaid@gmail.com",
    "amusukwa@gmail.com",
    "carolmkaysmamba14@gmail.com",
]

# Engagement tiers
TIERS = {
    "star":   {"min": 5, "label": "⭐ 5+ days",  "color": "#3fb950"},
    "active": {"min": 3, "label": "✅ 3–4 days", "color": "#58a6ff"},
    "low":    {"min": 1, "label": "⚠️ 1–2 days", "color": "#f0c040"},
    "silent": {"min": 0, "label": "❌ No updates","color": "#ff7b72"},
}


# ── Entry point ───────────────────────────────────────────────────────────────

def main(req: func.HttpRequest) -> func.HttpResponse:
    logging.info("GenerateWeeklyReport triggered")

    # Allow overriding week_start for testing past weeks
    try:
        body = req.get_json()
        week_start_str = body.get("week_start")
    except Exception:
        week_start_str = None

    if week_start_str:
        try:
            week_start = datetime.strptime(week_start_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            return _error("Invalid week_start format. Use YYYY-MM-DD.", 400)
    else:
        # Default: last full Monday-Sunday week
        today = datetime.now(timezone.utc).date()
        days_since_monday = today.weekday()
        last_monday = today - timedelta(days=days_since_monday + 7)
        week_start = datetime(last_monday.year, last_monday.month, last_monday.day, tzinfo=timezone.utc)

    week_end = week_start + timedelta(days=6)
    logging.info(f"Week: {week_start.date()} → {week_end.date()}")

    try:
        report = _build_weekly_report(week_start, week_end)
        _send_email(report)

        return func.HttpResponse(
            json.dumps({
                "success":   True,
                "weekStart": report["weekStart"],
                "weekEnd":   report["weekEnd"],
                "summary":   report["summary"],
                "sentTo":    TEST_RECIPIENTS,
            }, ensure_ascii=False, indent=2),
            status_code=200,
            mimetype="application/json",
        )
    except Exception as e:
        logging.error(f"GenerateWeeklyReport failed: {e}", exc_info=True)
        return _error(str(e), 500)


# ── Report builder ────────────────────────────────────────────────────────────

def _build_weekly_report(week_start: datetime, week_end: datetime) -> dict:
    client     = MongoClient(os.environ["COSMOS_CONNECTION_STRING"])
    updates    = client[DB_NAME][UPDATES_COL]
    cohort_col = client[DB_NAME][COHORT_COL]

    # Load cohort roster
    cohort     = list(cohort_col.find({"courseId": COURSE_ID}))
    id_to_name = {s["slackUserId"]: s["name"] for s in cohort if s.get("slackUserId")}
    all_students = {s["name"]: s.get("slackUserId") for s in cohort}
    logging.info(f"Cohort: {len(cohort)} students, {len(id_to_name)} with Slack ID")

    # Fetch all updates for the week
    start_str = week_start.strftime("%Y-%m-%d")
    end_str   = week_end.strftime("%Y-%m-%d")

    week_updates = list(updates.find({
        "dateStr": {"$gte": start_str, "$lte": end_str}
    }))
    logging.info(f"Found {len(week_updates)} updates for {start_str} → {end_str}")

    # Count unique active days per student
    student_days: dict[str, set]  = {}
    student_all:  dict[str, list] = {}

    for u in week_updates:
        sid = u.get("slackUserId", "")
        if not sid:
            continue
        student_days.setdefault(sid, set()).add(u.get("dateStr", ""))
        student_all.setdefault(sid, []).append(u)

    client.close()

    # Build per-student summaries
    summaries = []
    for name, slack_id in all_students.items():
        days_set   = student_days.get(slack_id, set()) if slack_id else set()
        days_count = len(days_set)

        # Most recent update this week
        latest = None
        if slack_id and slack_id in student_all:
            latest = sorted(student_all[slack_id], key=lambda u: u.get("messageTs", ""), reverse=True)[0]

        match      = latest.get("curriculumMatch") if latest else None
        chapter    = match.get("chapterTitle") if match else None
        chapter_id = match.get("chapterId")    if match else None
        snippet    = latest.get("messageText", "")[:150] if latest else None

        summaries.append({
            "name":       name,
            "slackUserId": slack_id,
            "daysActive": days_count,
            "tier":       _get_tier(days_count),
            "chapter":    chapter or "—",
            "chapterId":  chapter_id,
            "snippet":    snippet,
            "lastUpdate": latest.get("dateStr") if latest else None,
        })

    summaries.sort(key=lambda x: (-x["daysActive"], x["name"]))

    # Tier breakdown
    tier_counts = {"star": 0, "active": 0, "low": 0, "silent": 0}
    for s in summaries:
        tier_counts[s["tier"]] += 1

    total = len(cohort)

    return {
        "weekStart":  start_str,
        "weekEnd":    end_str,
        "courseId":   COURSE_ID,
        "summary": {
            "totalStudents": total,
            "anyUpdates":    sum(1 for s in summaries if s["daysActive"] > 0),
            "noUpdates":     sum(1 for s in summaries if s["daysActive"] == 0),
            "totalUpdates":  len(week_updates),
            "tierBreakdown": tier_counts,
        },
        "students": summaries,
        "greeting": _generate_greeting(summaries, start_str, end_str, tier_counts, total),
    }


# ── Greeting generator ────────────────────────────────────────────────────────

def _generate_greeting(
    summaries: list,
    week_start: str,
    week_end: str,
    tier_counts: dict,
    total: int,
) -> str:
    """
    Generates a unique weekly greeting using OpenRouter (deepseek-chat).
    Falls back to a default greeting if API call fails.
    """
    # Find most popular chapters this week
    chapter_counts: dict[str, int] = {}
    for s in summaries:
        if s["chapter"] != "—":
            chapter_counts[s["chapter"]] = chapter_counts.get(s["chapter"], 0) + 1

    top_chapters = sorted(chapter_counts.items(), key=lambda x: x[1], reverse=True)[:3]
    chapters_str = ", ".join(f"{ch} ({n} learners)" for ch, n in top_chapters) or "various topics"

    active = sum(1 for s in summaries if s["daysActive"] > 0)
    stars  = tier_counts.get("star", 0)

    prompt = f"""Write a short, warm and encouraging weekly greeting for mentors 
supporting coding learners in Namibia through CodeBlossom.

This week ({week_start} to {week_end}):
- {active} out of {total} learners posted daily updates
- {stars} learners were super active (5+ days!) 
- Most studied topics: {chapters_str}

Requirements:
- 2-3 sentences only
- Warm, personal, specific to the week's data
- Reference Namibia or CodeBlossom naturally if it fits
- 1-2 emojis max, no hashtags
- Vary the tone and opening each time — never start with "Hey" or "Hi Alice"
- End on an encouraging note for the mentoring team
- No quotation marks of any kind
- IMPORTANT: Return ONLY the greeting text itself, no alternatives, no variations, no asterisks, no markdown formatting, no quotes"""

    try:
        response = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {os.environ['OPENROUTER_API_KEY']}",
                "HTTP-Referer": "https://aitut.iverse.com",
                "X-Title": "AITut Weekly Report",
            },
            json={
                "model": "deepseek/deepseek-chat",
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 80,
                "temperature": 0.9,  # Higher = more creative variation each time
            },
            timeout=30,
        )
        response.raise_for_status()
        greeting = response.json()["choices"][0]["message"]["content"].strip()
        logging.info(f"Generated greeting: {greeting[:80]}...")
        return greeting

    except Exception as e:
        logging.error(f"Greeting generation failed: {type(e).__name__}: {e}")
        import traceback
        logging.error(traceback.format_exc())
        # Fallback greeting if OpenRouter fails
        return (
            f"Another week in the books! {active} out of {total} learners "
            f"shared their progress this week 🌱 "
            f"Keep up the amazing mentoring work!"
        )


# ── Email ─────────────────────────────────────────────────────────────────────

def _send_email(data: dict) -> None:
    sb = data["summary"]
    subject = (
        f"📊 Weekly Report — {data['weekStart']} → {data['weekEnd']} "
        f"({sb['anyUpdates']}/{sb['totalStudents']} active)"
    )
    html = _render_html(data)
    text = _render_text(data)
    sent, errors = send_email_to_list(TEST_RECIPIENTS, subject, html, text)
    logging.info(f"Email sent: {sent} ok, {errors} errors")


# ── HTML template ─────────────────────────────────────────────────────────────

def _render_html(data: dict) -> str:
    sb = data["summary"]
    td = data["tierBreakdown"] if "tierBreakdown" in data else sb["tierBreakdown"]

    # Student rows grouped by tier
    tier_order = ["star", "active", "low", "silent"]
    rows = ""
    current_tier = None

    for s in data["students"]:
        # Tier separator row
        if s["tier"] != current_tier:
            current_tier = s["tier"]
            tier_info  = TIERS[current_tier]
            rows += f"""
        <tr>
          <td colspan="3" style="padding:10px 12px 4px;
              background:#0d1117;color:{tier_info['color']};
              font-size:11px;font-weight:700;text-transform:uppercase;
              letter-spacing:1px;">
            {tier_info['label']}
          </td>
        </tr>"""

        # Days bar
        filled  = "█" * s["daysActive"]
        empty   = "░" * max(0, 7 - s["daysActive"])
        bar     = f'<span style="color:#3fb950;font-family:monospace;">{filled}</span>' \
                  f'<span style="color:#21262d;font-family:monospace;">{empty}</span>'

        chapter_color = "#3fb950" if s["chapterId"] else "#8b949e"
        snippet_html  = f'<div style="color:#8b949e;font-size:11px;margin-top:3px;">{s["snippet"][:120]}…</div>' \
                        if s["snippet"] else ""

        rows += f"""
        <tr style="border-bottom:1px solid #21262d;">
          <td style="padding:10px 12px;color:#e6edf3;font-size:13px;">
            {s['name']}
            <div style="font-size:10px;color:#8b949e;margin-top:2px;">{bar} {s['daysActive']}/7 days</div>
          </td>
          <td style="padding:10px 12px;color:{chapter_color};font-size:12px;white-space:nowrap;vertical-align:top;">
            {s['chapter']}
          </td>
          <td style="padding:10px 12px;vertical-align:top;">
            {snippet_html}
          </td>
        </tr>"""

    return f"""<!DOCTYPE html>
<html>
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0"></head>
<body style="margin:0;padding:0;background:#0d1117;font-family:'Helvetica Neue',Helvetica,Arial,sans-serif;">
<table width="100%" cellpadding="0" cellspacing="0" style="background:#0d1117;padding:32px 0;">
<tr><td align="center">
<table width="700" cellpadding="0" cellspacing="0"
  style="background:#161b22;border-radius:12px;border:1px solid #30363d;max-width:100%;">

  <!-- Header -->
  <tr><td style="background:#FF5F90;padding:28px 32px;border-radius:12px 12px 0 0;text-align:center;">
    <h1 style="margin:0;color:#fff;font-size:22px;font-weight:700;">📊 Weekly Mentor Report</h1>
    <p style="margin:6px 0 0;color:rgba(255,255,255,0.85);font-size:14px;">
      {data['weekStart']} → {data['weekEnd']} · CodeBlossom Full Stack Cohort · <em>Test mode</em>
    </p>
  </td></tr>

  <!-- Greeting -->
  <tr><td style="padding:20px 32px 0;">
    <div style="background:#21262d;border-radius:8px;padding:16px 20px;
                border-left:3px solid #FF5F90;">
      <p style="margin:0;color:#e6edf3;font-size:14px;line-height:1.6;font-style:italic;">
        {data.get('greeting', '')}
      </p>
    </div>
  </td></tr>

  <!-- Stats -->
  <tr><td style="padding:24px 32px 16px;">
    <table width="100%" cellpadding="0" cellspacing="0"><tr>
      <td width="18%" style="background:#21262d;border-radius:8px;padding:14px;text-align:center;">
        <div style="font-size:10px;color:#8b949e;text-transform:uppercase;letter-spacing:1px;">Total</div>
        <div style="font-size:26px;font-weight:700;color:#e6edf3;margin-top:4px;">{sb['totalStudents']}</div>
      </td><td width="2%"></td>
      <td width="18%" style="background:#21262d;border-radius:8px;padding:14px;text-align:center;">
        <div style="font-size:10px;color:#3fb950;text-transform:uppercase;letter-spacing:1px;">Active</div>
        <div style="font-size:26px;font-weight:700;color:#3fb950;margin-top:4px;">{sb['anyUpdates']}</div>
      </td><td width="2%"></td>
      <td width="18%" style="background:#21262d;border-radius:8px;padding:14px;text-align:center;">
        <div style="font-size:10px;color:#ff7b72;text-transform:uppercase;letter-spacing:1px;">Silent</div>
        <div style="font-size:26px;font-weight:700;color:#ff7b72;margin-top:4px;">{sb['noUpdates']}</div>
      </td><td width="2%"></td>
      <td width="18%" style="background:#21262d;border-radius:8px;padding:14px;text-align:center;">
        <div style="font-size:10px;color:#3fb950;text-transform:uppercase;letter-spacing:1px;">⭐ 5+ days</div>
        <div style="font-size:26px;font-weight:700;color:#3fb950;margin-top:4px;">{sb['tierBreakdown']['star']}</div>
      </td><td width="2%"></td>
      <td width="18%" style="background:#21262d;border-radius:8px;padding:14px;text-align:center;">
        <div style="font-size:10px;color:#f0c040;text-transform:uppercase;letter-spacing:1px;">Total updates</div>
        <div style="font-size:26px;font-weight:700;color:#f0c040;margin-top:4px;">{sb['totalUpdates']}</div>
      </td>
    </tr></table>
  </td></tr>

  <!-- Student table -->
  <tr><td style="padding:0 32px 32px;">
    <h2 style="color:#e6edf3;font-size:15px;margin:0 0 12px;">👥 Student Engagement</h2>
    <table width="100%" cellpadding="0" cellspacing="0"
      style="background:#21262d;border-radius:8px;border:1px solid #30363d;">
      <tr style="background:#161b22;border-radius:8px 8px 0 0;">
        <th style="padding:8px 12px;color:#8b949e;font-size:11px;text-align:left;text-transform:uppercase;width:30%;">Student</th>
        <th style="padding:8px 12px;color:#8b949e;font-size:11px;text-align:left;text-transform:uppercase;width:20%;">Last Chapter</th>
        <th style="padding:8px 12px;color:#8b949e;font-size:11px;text-align:left;text-transform:uppercase;">Latest Update</th>
      </tr>
      {rows}
    </table>
  </td></tr>

  <!-- Footer -->
  <tr><td style="padding:16px 32px;border-top:1px solid #21262d;text-align:center;">
    <p style="color:#6b7280;font-size:11px;margin:0;">
      Generated automatically by AITut.Iverse · Week of {data['weekStart']}
    </p>
  </td></tr>

</table>
</td></tr>
</table>
</body>
</html>"""


def _render_text(data: dict) -> str:
    sb = data["summary"]
    lines = [
        f"Weekly Mentor Report — {data['weekStart']} → {data['weekEnd']}",
        "=" * 50,
        f"Total: {sb['totalStudents']}  Active: {sb['anyUpdates']}  Silent: {sb['noUpdates']}",
        f"⭐ 5+ days: {sb['tierBreakdown']['star']}  "
        f"✅ 3-4: {sb['tierBreakdown']['active']}  "
        f"⚠️ 1-2: {sb['tierBreakdown']['low']}  "
        f"❌ 0: {sb['tierBreakdown']['silent']}",
        "",
    ]
    for s in data["students"]:
        bar = "█" * s["daysActive"] + "░" * (7 - s["daysActive"])
        lines.append(f"{s['name']:<35} {bar} {s['daysActive']}/7  {s['chapter']}")
    return "\n".join(lines)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _get_tier(days: int) -> str:
    if days >= 5: return "star"
    if days >= 3: return "active"
    if days >= 1: return "low"
    return "silent"


def _error(msg: str, status: int) -> func.HttpResponse:
    return func.HttpResponse(
        json.dumps({"success": False, "error": msg}),
        status_code=status,
        mimetype="application/json",
    )