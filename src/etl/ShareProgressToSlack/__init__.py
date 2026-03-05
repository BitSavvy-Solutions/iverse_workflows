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
from shared.github_service import get_material_title


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
        circle_members_collection = user_db['circleMembers']
        circles_collection = user_db['circles']
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
        
        # ========================================
        # GET USER'S CIRCLE AND SLACK CHANNEL
        # ========================================
        circle_membership = circle_members_collection.find_one({
            'userId': user_id
        })
        
        if not circle_membership:
            client.close()
            return func.HttpResponse(
                json.dumps({
                    "success": False,
                    "error": "You are not part of any learning circle. Please join a circle to share updates."
                }),
                status_code=400,
                mimetype="application/json"
            )
        
        user_circle_id = circle_membership.get('circleId')
        logging.info(f'User belongs to circle: {user_circle_id}')
        
        # Get circle's Slack channel
        circle = circles_collection.find_one({'circleId': user_circle_id})
        
        if not circle:
            client.close()
            return func.HttpResponse(
                json.dumps({
                    "success": False,
                    "error": "Circle configuration not found"
                }),
                status_code=404,
                mimetype="application/json"
            )
        
        circle_slack_channel = circle.get('slackChannel')
        
        if not circle_slack_channel:
            client.close()
            return func.HttpResponse(
                json.dumps({
                    "success": False,
                    "error": "Your circle does not have a Slack channel configured. Please contact your mentor."
                }),
                status_code=400,
                mimetype="application/json"
            )
        
        logging.info(f'Sharing to circle Slack channel: {circle_slack_channel}')
        
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
                report_date=datetime.now(timezone.utc).strftime('%Y-%m-%d'),
                slack_channel_id=circle_slack_channel
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
        
        # ========================================
        # GROUP BY MATERIAL - Aggregate multiple entries
        # ========================================

        material_groups = {}

        for progress in progress_entries:
            chapter_id = progress.get('chapterId', '')
            material_id = progress.get('materialId', '')
            key = f"{chapter_id}/{material_id}"
            
            if key not in material_groups:
                material_groups[key] = {
                    'chapterId': chapter_id,
                    'materialId': material_id,
                    'timeSpent': 0,
                    'completed': False,
                    'updateTexts': []
                }
            
            # Accumulate time
            material_groups[key]['timeSpent'] += progress.get('timeSpentSeconds', 0)
            
            # If any entry is completed, mark as completed
            if progress.get('isCompleted', False):
                material_groups[key]['completed'] = True
            
            # Collect all update texts
            update_text = progress.get('updateText', '')
            if update_text and update_text.strip():
                material_groups[key]['updateTexts'].append(update_text)

        logging.info(f'Grouped into {len(material_groups)} unique materials')

        # Calculate summary from grouped data
        total_time_seconds = sum(m['timeSpent'] for m in material_groups.values())
        completed_count = sum(1 for m in material_groups.values() if m['completed'])
        inprogress_count = sum(1 for m in material_groups.values() if not m['completed'])

        # Format materials for Slack
        completed_materials = []

        for material in material_groups.values():
            chapter_id = material['chapterId']
            material_id = material['materialId']
            is_completed = material['completed']
            
            # Get material title from GitHub
            material_title = get_material_title(chapter_id, material_id)
            
            if material_title:
                display_text = material_title
            else:
                display_text = f"{chapter_id}/{material_id}"
            
            status_emoji = "✅" if is_completed else "⏳"
            formatted_text = f"{status_emoji} {display_text}"
            
            # Add to list
            completed_materials.append({
                'updateText': formatted_text,
                'isCompleted': is_completed,
                'materialTitle': display_text,
                'comments': material['updateTexts']  # Store comments
            })

        # Add user comment to materials list (if provided)
        if user_comment:
            # Parse user comment - it already contains formatted material comments
            # Just add as-is to the beginning
            completed_materials.insert(0, {
                'updateText': f"💭 {user_comment}",
                'isCompleted': False
            })

        # Prepare report data for Slack
        report_data = {
            'totalTimeSpent': total_time_seconds,
            'completedCount': completed_count,
            'inprogressCount': inprogress_count,
            'completedMaterials': completed_materials
        }

        # Send to Slack
        success = send_daily_report_to_slack(
            user_name=user_name,
            user_email=user_email,
            report_data=report_data,
            report_date=datetime.now(timezone.utc).strftime('%Y-%m-%d'),
            slack_channel_id=circle_slack_channel
        )

        client.close()

        if success:
            logging.info(f'✅ Daily progress shared to Slack for user: {user_name}')
            
            # Update database: mark as shared (MANUAL)
            progress_collection.update_many(
                {
                    'userId': user_id,
                    'createdAt': {
                        '$gte': start_time,
                        '$lte': end_time
                    }
                },
                {
                    '$set': {
                        'sharedToSlack': True,
                        'sharedAt': datetime.now(timezone.utc),
                        'sharedType': 'manual'
                    }
                }
            )
            
            return func.HttpResponse(
                json.dumps({
                    "success": True,
                    "message": f"Your daily update with {len(material_groups)} activities shared to Slack community! 🎉",
                    "activitiesCount": len(material_groups),  # Unique materials count
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