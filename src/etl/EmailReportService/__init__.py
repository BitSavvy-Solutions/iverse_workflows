import logging
import azure.functions as func
from datetime import datetime, timedelta, timezone
import os
import json
from pymongo import MongoClient
from typing import List, Dict, Any, Optional
from shared.email_service import send_email

def main(req: func.HttpRequest) -> func.HttpResponse:
    """
    HTTP trigger to send daily report emails.
    
    Request Body (JSON):
    {
        "report_date": "2025-11-20",  // Optional: specific date (YYYY-MM-DD)
        "circle_id": "circle123"       // Optional: specific circle
    }
    """
    start_time = datetime.now(timezone.utc)
    logging.info(f'INFO - Email Report Service Started: {start_time.isoformat()}')

    try:
        # Parse request parameters
        req_body = {}
        try:
            req_body = req.get_json()
        except ValueError:
            pass  # No body or invalid JSON, use defaults
        
        report_date = req_body.get('report_date')
        circle_id_filter = req_body.get('circle_id')
        
        # Default to yesterday if not specified
        if not report_date:
            yesterday = datetime.now(timezone.utc) - timedelta(days=1)
            report_date = yesterday.strftime('%Y-%m-%d')
        
        logging.info(f'Parameters: report_date={report_date}, circle_id={circle_id_filter or "all"}')
        
        # Fetch reports from database
        reports = fetch_reports(report_date, circle_id_filter)
        
        if len(reports) == 0:
            logging.info(f'No reports found for date {report_date}')
            return func.HttpResponse(
                json.dumps({
                    "success": True,
                    "message": "!!-- No reports found --!!",
                    "report_date": report_date,
                    "emails_sent": 0
                }),
                status_code=200,
                mimetype="application/json"
            )
        
        logging.info(f'Found {len(reports)} reports to process')
        
        # Process each report
        emails_sent = 0
        errors = 0
        
        for report in reports:
            try:
                # Get mentor emails for this circle
                recipients = get_recipients(report['circleId'])
                
                if len(recipients) == 0:
                    logging.warning(f'No recipients for report {report["reportId"]}, skipping')
                    continue
                
                # Create email content (using circleId instead of circleName)
                subject = f'Daily Progress Report: {report["circleId"]} - {report["reportDate"]}'

                # Get summary data
                summary = report.get('summary', {})
                total_students = summary.get('totalStudents', 0)
                active_students = summary.get('activeStudents', 0)
                materials_completed = summary.get('materialsCompleted', 0)
                total_time = summary.get('totalTimeSpentSeconds', 0)
                completion_rate = summary.get('completionRate', 0)
                
                # Convert seconds to hours
                total_hours = round(total_time / 3600, 1)
                
                body_html = f"""
                <!DOCTYPE html>
                <html>
                <head>
                    <meta charset="UTF-8">
                    <meta name="viewport" content="width=device-width, initial-scale=1.0">
                </head>
                <body style="margin: 0; padding: 0; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif; background-color: #f5f5f5;">
                    <table width="100%" cellpadding="0" cellspacing="0" border="0" style="background-color: #f5f5f5; padding: 20px;">
                        <tr>
                            <td align="center">
                                <!-- Main Container -->
                                <table width="600" cellpadding="0" cellspacing="0" border="0" style="background-color: #ffffff; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1);">
                                    
                                    <!-- Header -->
                                    <tr>
                                        <td style="background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); padding: 30px; border-radius: 8px 8px 0 0;">
                                            <h1 style="margin: 0; color: #ffffff; font-size: 24px; font-weight: 600;">
                                                📊 Daily Progress Report
                                            </h1>
                                            <p style="margin: 8px 0 0 0; color: #e0e7ff; font-size: 14px;">
                                                {report["reportDate"]}
                                            </p>
                                        </td>
                                    </tr>
                                    
                                    <!-- Circle Info -->
                                    <tr>
                                        <td style="padding: 30px;">
                                            <table width="100%" cellpadding="0" cellspacing="0" border="0">
                                                <tr>
                                                    <td style="padding-bottom: 20px; border-bottom: 2px solid #e5e7eb;">
                                                        <h2 style="margin: 0; font-size: 18px; color: #1f2937; font-weight: 600;">
                                                            Circle: {report["circleId"]}
                                                        </h2>
                                                    </td>
                                                </tr>
                                            </table>
                                        </td>
                                    </tr>
                                    
                                    <!-- Key Metrics -->
                                    <tr>
                                        <td style="padding: 0 30px 30px 30px;">
                                            <table width="100%" cellpadding="0" cellspacing="0" border="0">
                                                
                                                <!-- Metric Row 1 -->
                                                <tr>
                                                    <td width="50%" style="padding: 15px; background-color: #f9fafb; border-radius: 6px;">
                                                        <p style="margin: 0; color: #6b7280; font-size: 13px; font-weight: 500; text-transform: uppercase; letter-spacing: 0.5px;">
                                                            Total Students
                                                        </p>
                                                        <p style="margin: 5px 0 0 0; color: #1f2937; font-size: 28px; font-weight: 700;">
                                                            {total_students}
                                                        </p>
                                                    </td>
                                                    <td width="10"></td>
                                                    <td width="50%" style="padding: 15px; background-color: #f0fdf4; border-radius: 6px;">
                                                        <p style="margin: 0; color: #15803d; font-size: 13px; font-weight: 500; text-transform: uppercase; letter-spacing: 0.5px;">
                                                            Active Students
                                                        </p>
                                                        <p style="margin: 5px 0 0 0; color: #166534; font-size: 28px; font-weight: 700;">
                                                            {active_students}
                                                        </p>
                                                    </td>
                                                </tr>
                                                
                                                <tr><td colspan="3" height="15"></td></tr>
                                                
                                                <!-- Metric Row 2 -->
                                                <tr>
                                                    <td style="padding: 15px; background-color: #eff6ff; border-radius: 6px;">
                                                        <p style="margin: 0; color: #1e40af; font-size: 13px; font-weight: 500; text-transform: uppercase; letter-spacing: 0.5px;">
                                                            Materials Completed
                                                        </p>
                                                        <p style="margin: 5px 0 0 0; color: #1e3a8a; font-size: 28px; font-weight: 700;">
                                                            {materials_completed}
                                                        </p>
                                                    </td>
                                                    <td width="10"></td>
                                                    <td style="padding: 15px; background-color: #fef3c7; border-radius: 6px;">
                                                        <p style="margin: 0; color: #92400e; font-size: 13px; font-weight: 500; text-transform: uppercase; letter-spacing: 0.5px;">
                                                            Total Time Spent
                                                        </p>
                                                        <p style="margin: 5px 0 0 0; color: #78350f; font-size: 28px; font-weight: 700;">
                                                            {total_hours}h
                                                        </p>
                                                    </td>
                                                </tr>
                                                
                                                <tr><td colspan="3" height="15"></td></tr>
                                                
                                                <!-- Completion Rate -->
                                                <tr>
                                                    <td colspan="3" style="padding: 15px; background-color: #faf5ff; border-radius: 6px;">
                                                        <p style="margin: 0; color: #6b21a8; font-size: 13px; font-weight: 500; text-transform: uppercase; letter-spacing: 0.5px;">
                                                            Completion Rate
                                                        </p>
                                                        <p style="margin: 5px 0 0 0; color: #581c87; font-size: 28px; font-weight: 700;">
                                                            {int(completion_rate * 100)}%
                                                        </p>
                                                    </td>
                                                </tr>
                                                
                                            </table>
                                        </td>
                                    </tr>
                                    
                                    <!-- Footer -->
                                    <tr>
                                        <td style="padding: 20px 30px; background-color: #f9fafb; border-radius: 0 0 8px 8px; border-top: 1px solid #e5e7eb;">
                                            <p style="margin: 0; color: #6b7280; font-size: 12px; text-align: center;">
                                                This is an automated report from <strong>AITUT Learning Platform</strong>
                                            </p>
                                            <p style="margin: 8px 0 0 0; color: #9ca3af; font-size: 11px; text-align: center;">
                                                Report generated at {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}
                                            </p>
                                        </td>
                                    </tr>
                                    
                                </table>
                            </td>
                        </tr>
                    </table>
                </body>
                </html>
                """
                
                # Plain text version (fallback)
                body_text = f"""
                ═══════════════════════════════════════════
                📊 DAILY PROGRESS REPORT
                ═══════════════════════════════════════════

                Circle: {report["circleId"]}
                Date: {report["reportDate"]}

                ─────────────────────────────────────────

                KEY METRICS:

                - Total Students: {total_students}
                - Active Students: {active_students}
                - Materials Completed: {materials_completed}
                - Total Time Spent: {total_hours} hours
                - Completion Rate: {int(completion_rate * 100)}%

                ─────────────────────────────────────────

                This is an automated report from AITUT Learning Platform
                Report generated at {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}

                ═══════════════════════════════════════════
                """
                
                # Send email
                success = send_email(recipients, subject, body_html, body_text)
                
                if success:
                    emails_sent += 1
                else:
                    errors += 1
                    
            except Exception as e:
                logging.error(f'Error processing report {report["reportId"]}: {str(e)}', exc_info=True)
                errors += 1
        
        # Return response
        duration = (datetime.now(timezone.utc) - start_time).total_seconds()
        response_data = {
            "success": True,
            "message": "Email sending completed",
            "report_date": report_date,
            "reports_processed": len(reports),
            "emails_sent": emails_sent,
            "errors": errors,
            "duration_seconds": round(duration, 2)
        }
        
        logging.info(f'Email Report Service Complete: {json.dumps(response_data)}')
        
        return func.HttpResponse(
            json.dumps(response_data),
            status_code=200,
            mimetype="application/json"
        )
        
    except Exception as error:
        logging.error(f'Fatal error in email service: {str(error)}', exc_info=True)
        return func.HttpResponse(
            json.dumps({
                "success": False,
                "error": str(error)
            }),
            status_code=500,
            mimetype="application/json"
        )


def fetch_reports(report_date: str, circle_id_filter: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    Fetch reports from database for a specific date.
    Only returns reports for circles with emailUpdates=true.
    
    Args:
        report_date: Date string in YYYY-MM-DD format
        circle_id_filter: Optional specific circle ID to fetch
        
    Returns:
        List of report documents (filtered by emailUpdates)
    """
    client = None
    
    try:
        # Connect to Cosmos DB
        connection_string = os.environ['COSMOS_CONNECTION_STRING']
        client = MongoClient(connection_string)
        logging.info('Connected to Cosmos DB')
        
        user_db = client['userdb']
        course_db = client['coursedb']

        circles_collection = user_db['circles']
        reports_collection = course_db['reports']
        
        # Step 1: Get circles with emailUpdates=true
        circle_query = {'emailUpdates': True}
        if circle_id_filter:
            circle_query['circleId'] = circle_id_filter
        
        circles_with_email = circles_collection.find(circle_query)
        eligible_circle_ids = [doc['circleId'] for doc in circles_with_email]
        
        if len(eligible_circle_ids) == 0:
            logging.info(f'No circles with emailUpdates=true found')
            return []
        
        logging.info(f'Found {len(eligible_circle_ids)} circles with emailUpdates=true')
        logging.info(f'Eligible circles: {", ".join(eligible_circle_ids)}')
        
        # Step 2: Build query, fetch reports only for eligible circles
        query = {
            'reportDate': report_date,
            'circleId': {'$in': eligible_circle_ids}
        }
        
        if circle_id_filter:
            logging.info(f'Fetching report for circle: {circle_id_filter}, date: {report_date}')
        else:
            logging.info(f'Fetching all reports for {len(eligible_circle_ids)} circles with emailUpdates=true, date: {report_date}')
        
        # Execute query
        reports = list(reports_collection.find(query))
        
        logging.info(f'Found {len(reports)} reports for circles with emailUpdates=true')
        
        return reports
        
    except Exception as error:
        logging.error(f'Error fetching reports: {str(error)}', exc_info=True)
        raise  # Re-raise to mark function execution as failed
        
    finally:
        if client:
            client.close()
            logging.info('Database connection closed')

def get_recipients(circle_id: str) -> List[str]:
    """
    Get mentor email addresses for a specific circle.
    
    Args:
        circle_id: Circle ID to fetch mentors for
        
    Returns:
        List of mentor email addresses
    """
    client = None
    
    try:
        # Connect to Cosmos DB
        connection_string = os.environ['COSMOS_CONNECTION_STRING']
        client = MongoClient(connection_string)
        
        user_db = client['userdb']
        circle_members_collection = user_db['circleMembers']
        users_collection = user_db['users']
        
        # Step 1: Get mentor userIds from circles collection
        mentor_memberships = circle_members_collection.find({
            'circleId': circle_id,
        })
        
        mentor_user_ids = [doc['userId'] for doc in mentor_memberships]
        
        if len(mentor_user_ids) == 0:
            logging.warning(f'No mentors found for circle {circle_id}')
            return []
        
        logging.info(f'Found {len(mentor_user_ids)} mentors for circle {circle_id}')
        
        # Step 2: Get emails from users collection
        user_docs = users_collection.find({
            'userId': {'$in': mentor_user_ids}
        })
        
        emails = []
        for user in user_docs:
            email = user.get('email')
            if email:
                emails.append(email)
            else:
                logging.warning(f'User {user.get("userId")} has no email address')
        
        logging.info(f'Retrieved {len(emails)} mentor emails')
        logging.info(f'DEBUG - Full recipient list: {emails}')
        
        return emails
        
    except Exception as error:
        logging.error(f'Error fetching recipients for circle {circle_id}: {str(error)}', exc_info=True)
        raise
        
    finally:
        if client:
            client.close()