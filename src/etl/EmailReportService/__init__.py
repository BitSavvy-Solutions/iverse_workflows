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
                
                body_html = f"""
                <html>
                    <body>
                        <h2>Daily Progress Report</h2>
                        <p><strong>Circle:</strong> {report["circleId"]}</p>
                        <p><strong>Date:</strong> {report["reportDate"]}</p>
                        <p><strong>Active Students:</strong> {report["summary"]["activeStudents"]}</p>
                        <p><strong>Materials Completed:</strong> {report["summary"]["materialsCompleted"]}</p>
                    </body>
                </html>
                """
                
                body_text = f"""
                Daily Progress Report
                
                Circle: {report["circleId"]}
                Date: {report["reportDate"]}
                Active Students: {report["summary"]["activeStudents"]}
                Materials Completed: {report["summary"]["materialsCompleted"]}
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
    
    Args:
        report_date: Date string in YYYY-MM-DD format
        circle_id_filter: Optional specific circle ID to fetch
        
    Returns:
        List of report documents
    """
    client = None
    
    try:
        # Connect to Cosmos DB
        connection_string = os.environ['COSMOS_CONNECTION_STRING']
        client = MongoClient(connection_string)
        logging.info('Connected to Cosmos DB')
        
        course_db = client['coursedb']
        reports_collection = course_db['reports']
        
        # Build query
        query = {'reportDate': report_date}
        
        if circle_id_filter:
            query['circleId'] = circle_id_filter
            logging.info(f'Fetching report for circle: {circle_id_filter}, date: {report_date}')
        else:
            logging.info(f'Fetching all reports for date: {report_date}')
        
        # Execute query
        reports = list(reports_collection.find(query))
        
        logging.info(f'Found {len(reports)} reports')
        
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
            'role': 'mentor'
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