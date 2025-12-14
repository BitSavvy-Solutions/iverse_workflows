"""
Azure Function to share daily progress summary to Slack
Triggered by student clicking "Share Daily Update" button
"""
import logging
import azure.functions as func
import json
from datetime import datetime, timedelta, timezone
from pymongo import MongoClient
import os
from shared.slack_service import send_daily_report_to_slack


def main(req: func.HttpRequest) -> func.HttpResponse:
    """
    HTTP trigger to share student's daily progress to Slack
    
    Request Body (JSON):
    {
        "userId": "cdadab2a-d6c3-4270-ac47-ac70831cb851",
        "comment": "Optional personal note"  // Optional
    }
    """
    logging.info('ShareProgressToSlack function triggered')
    
    try:
        # Parse request body
        req_body = req.get_json()
        
        user_id = req_body.get('userId')
        user_comment = req_body.get('comment', '')  # Optional comment
        
        if not user_id:
            return func.HttpResponse(
                json.dumps({
                    "success": False, 
                    "error": "userId is required"
                }),
                status_code=400,
                mimetype="application/json"
            )
        
        logging.info(f'Sharing daily progress for user: {user_id}')
        
        # Connect to database
        client = MongoClient(os.environ['COSMOS_CONNECTION_STRING'])
        user_db = client['userdb']
        course_db = client['coursedb']
        
        
        users_collection = user_db['users']
        progress_collection = course_db['progress']
        
        # TEST: Get ALL progress for this user (no date filter)
        test_entries = list(progress_collection.find({'userId': user_id}))
        logging.info(f'🧪 TEST: Found {len(test_entries)} total progress entries for user')

        if len(test_entries) > 0:
            logging.info(f'🧪 TEST: Sample entry createdAt: {test_entries[0].get("createdAt")}')
            logging.info(f'🧪 TEST: Sample entry type: {type(test_entries[0].get("createdAt"))}')
                
        
        # Get user info
        user = users_collection.find_one({'userId': user_id})
        
        if not user:
            client.close()
            return func.HttpResponse(
                json.dumps({"success": False, "error": "User not found"}),
                status_code=404,
                mimetype="application/json"
            )
        
        user_name = (user.get('profile') or {}).get('name') or user.get('name', 'Student')
        user_email = user.get('email')
        
        if not user_email:
            client.close()
            return func.HttpResponse(
                json.dumps({
                    "success": False, 
                    "error": "User email not found. Cannot find Slack account."
                }),
                status_code=400,
                mimetype="application/json"
            )
        
        # Get all progress entries from last 24 hours
        end_time = datetime.now(timezone.utc)
        start_time = end_time - timedelta(hours=24)

        logging.info(f'Querying progress from {start_time} to {end_time}')

        # Query using datetime objects directly
        progress_entries = list(progress_collection.find({
            'userId': user_id,
            'createdAt': {
                '$gte': start_time,
                '$lte': end_time
            }
        }))
        
        # Allow sharing even without progress if user has comments
        if len(progress_entries) == 0:
            logging.info(f'No progress entries found, but allowing share with comments only')
            
            # Only send if user provided a comment
            if not user_comment or user_comment.strip() == '':
                client.close()
                return func.HttpResponse(
                    json.dumps({
                        "success": False, 
                        "error": "No progress found for today and no comments provided"
                    }),
                    status_code=404,
                    mimetype="application/json"
                )
            
            # Prepare minimal report data with just comments
            report_data = {
                'totalTimeSpent': 0,
                'completedCount': 0,
                'inprogressCount': 0,
                'completedMaterials': [
                    {
                        'updateText': f"📝 No course progress today",
                        'isCompleted': False
                    },
                    {
                        'updateText': f"💭 {user_comment}",
                        'isCompleted': False
                    }
                ]
            }
            
            # Send to Slack
            success = send_daily_report_to_slack(
                user_name=user_name,
                user_email=user_email,
                report_data=report_data,
                report_date=datetime.now(timezone.utc).strftime('%Y-%m-%d')
            )
            
            client.close()
            
            if success:
                logging.info(f'✅ Comment-only update shared to Slack for user: {user_name}')
                return func.HttpResponse(
                    json.dumps({
                        "success": True,
                        "message": "Your update shared to Slack community! 🎉",
                        "activitiesCount": 0,
                        "completedCount": 0,
                        "inprogressCount": 0,
                        "totalTimeSeconds": 0
                    }),
                    status_code=200,
                    mimetype="application/json"
                )
            else:
                return func.HttpResponse(
                    json.dumps({
                        "success": False,
                        "error": "Failed to send message to Slack"
                    }),
                    status_code=500,
                    mimetype="application/json"
                )

        # Continue with normal flow if progress exists...
        logging.info(f'Found {len(progress_entries)} progress entries for user {user_id}')
        
        # Calculate summary
        total_time_seconds = sum(p.get('timeSpentSeconds', 0) for p in progress_entries)
        completed_count = sum(1 for p in progress_entries if p.get('isCompleted', False))
        inprogress_count = sum(1 for p in progress_entries if not p.get('isCompleted', False))
        
        # Format completed materials
        completed_materials = []
        for progress in progress_entries:
            chapter_id = progress.get('chapterId', '')
            material_id = progress.get('materialId', '')
            update_text = progress.get('updateText', 'Progress made')
            is_completed = progress.get('isCompleted', False)
            
            status_emoji = "✅" if is_completed else "⏳"
            formatted_text = f"{status_emoji} {chapter_id}/{material_id}: {update_text}"
            
            completed_materials.append({
                'updateText': formatted_text,
                'isCompleted': is_completed
            })
        
        # Add user comment if provided
        if user_comment:
            completed_materials.append({
                'updateText': f"💭 {user_comment}",
                'isCompleted': False
            })
        
        # Prepare report data for Slack
        report_data = {
            'totalTimeSpent': total_time_seconds,
            'completedCount': completed_count,
            'completedMaterials': completed_materials,
            'inprogressCount': inprogress_count
        }
        
        # Send to Slack
        success = send_daily_report_to_slack(
            user_name=user_name,
            user_email=user_email,
            report_data=report_data,
            report_date=datetime.now(timezone.utc).strftime('%Y-%m-%d')
        )
        
        client.close()
        
        if success:
            logging.info(f'✅ Daily progress shared to Slack for user: {user_name}')
            return func.HttpResponse(
                json.dumps({
                    "success": True,
                    "message": f"Your daily update with {len(progress_entries)} activities shared to Slack community! 🎉",
                    "activitiesCount": len(progress_entries),
                    "completedCount": completed_count,
                    "inprogressCount": inprogress_count,
                    "totalTimeSeconds": total_time_seconds
                }),
                status_code=200,
                mimetype="application/json"
            )
        else:
            logging.error(f'❌ Failed to share progress to Slack for user: {user_name}')
            return func.HttpResponse(
                json.dumps({
                    "success": False,
                    "error": "Failed to send message to Slack"
                }),
                status_code=500,
                mimetype="application/json"
            )
        
    except ValueError as e:
        logging.error(f'Invalid JSON in request body: {str(e)}')
        return func.HttpResponse(
            json.dumps({"success": False, "error": "Invalid JSON"}),
            status_code=400,
            mimetype="application/json"
        )
    except Exception as e:
        logging.error(f'Error in ShareProgressToSlack: {str(e)}', exc_info=True)
        return func.HttpResponse(
            json.dumps({
                "success": False,
                "error": str(e)
            }),
            status_code=500,
            mimetype="application/json"
        )