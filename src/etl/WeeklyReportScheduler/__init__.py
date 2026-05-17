"""
src/etl/WeeklyReportScheduler/__init__.py
──────────────────────────────────────────
Timer trigger that runs every Monday at 6:00 UTC (8:00 CAT — Malawi/Namibia time).
Calls the same logic as GenerateWeeklyReport HTTP function.

To test manually use GenerateWeeklyReport HTTP endpoint instead:
  curl -X POST http://localhost:7071/api/GenerateWeeklyReport \
       -H "Content-Type: application/json" \
       -d '{"week_start": "2026-05-05"}'
"""

import logging
from datetime import datetime, timezone, timedelta

import azure.functions as func

# Import shared logic from GenerateWeeklyReport
from GenerateWeeklyReport import _build_weekly_report, _send_email


def main(mytimer: func.TimerRequest) -> None:
    logging.info("WeeklyReportScheduler triggered")

    if mytimer.past_due:
        logging.warning("Timer is past due — running anyway")

    # Always report on the previous full Mon-Sun week
    today = datetime.now(timezone.utc).date()
    days_since_monday = today.weekday()
    last_monday = today - timedelta(days=days_since_monday + 7)
    week_start = datetime(
        last_monday.year, last_monday.month, last_monday.day,
        tzinfo=timezone.utc
    )
    week_end = week_start + timedelta(days=6)

    logging.info(f"Generating report for week: {week_start.date()} → {week_end.date()}")

    try:
        report = _build_weekly_report(week_start, week_end)
        _send_email(report)
        logging.info("Weekly report sent successfully")
    except Exception as e:
        logging.error(f"WeeklyReportScheduler failed: {e}", exc_info=True)