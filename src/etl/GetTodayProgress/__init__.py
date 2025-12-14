"""
Azure Function to get today's progress for a student
"""
import logging
import azure.functions as func
import json
from datetime import datetime, timezone, timedelta
from pymongo import MongoClient
import os
from typing import Any


def main(req: func.HttpRequest) -> func.HttpResponse:
    """
    HTTP trigger to get student's progress for today
    
    Query Parameters:
    - userId: Student's user ID
    
    Returns:
    {
        "success": true,
        "data": {
            "modulesCompleted": 3,
            "timeSpent": 9000,
            "totalActivities": 4,
            "activities": [...]
        }
    }
    """
    logging.info('GetTodayProgress function triggered')
    
    try:
        # Get userId from query parameters
        user_id = req.params.get('userId')
        
        if not user_id:
            return func.HttpResponse(
                json.dumps({
                    "success": False,
                    "error": "userId parameter is required"
                }),
                status_code=400,
                mimetype="application/json"
            )
        
        logging.info(f'Getting today progress for user: {user_id}')
        
        # Connect to database
        client = MongoClient(os.environ['COSMOS_CONNECTION_STRING'])
        course_db = client['coursedb']
        progress_collection = course_db['progress']
        
        # Get today's range (last 24 hours)
        end_time = datetime.now(timezone.utc)
        start_time = end_time - timedelta(hours=24)
        
        # Query progress entries for today
        progress_entries = list(progress_collection.find({
            'userId': user_id,
            'createdAt': {
                '$gte': start_time,
                '$lte': end_time
            }
        }))
        
        logging.info(f'Found {len(progress_entries)} progress entries for today')
        
        # Calculate statistics
        modules_completed = sum(1 for p in progress_entries if p.get('isCompleted', False))
        modules_in_progress = sum(1 for p in progress_entries if not p.get('isCompleted', False))
        total_time_spent = sum(p.get('timeSpentSeconds', 0) for p in progress_entries)
        
        # Helper: normalize createdAt values that might be datetimes or raw mongo $date dicts
        def _to_datetime(value: Any):
            # If already a datetime, return it
            if isinstance(value, datetime):
                return value
            # Mongo export sometimes uses {'$date': millis}
            if isinstance(value, dict) and '$date' in value:
                try:
                    millis = int(value['$date'])
                    return datetime.fromtimestamp(millis / 1000.0, tz=timezone.utc)
                except Exception:
                    return None
            return None

        # Sort progress entries by creation time (most recent first) using normalized datetimes
        progress_entries.sort(key=lambda p: _to_datetime(p.get('createdAt')) or datetime.fromtimestamp(0, tz=timezone.utc), reverse=True)

        # Format activities for frontend
        activities = []
        for progress in progress_entries:
            chapter_id = progress.get('chapterId', '')
            material_id = progress.get('materialId', '')
            update_text = progress.get('updateText', 'Progress update')
            is_completed = progress.get('isCompleted', False)
            
            # Create readable title
            title = f"{chapter_id}/{material_id}: {update_text}"
            
            # Normalize createdAt into an ISO-8601 string so json.dumps won't fail
            created_at_val = progress.get('createdAt')
            created_at_dt = _to_datetime(created_at_val)
            created_at_serializable = created_at_dt.isoformat() if created_at_dt else created_at_val

            activities.append({
                'id': progress.get('progressId'),
                'title': title,
                'completed': is_completed,
                'chapterId': chapter_id,
                'materialId': material_id,
                'timeSpent': progress.get('timeSpentSeconds', 0),
                'createdAt': created_at_serializable
            })
        
        # activities already follow progress_entries order; no additional sort needed
        
        client.close()
        
        return func.HttpResponse(
            json.dumps({
                "success": True,
                "data": {
                "modulesCompleted": modules_completed,
                "modulesInProgress": modules_in_progress,  # ← Добавь это
                "timeSpent": total_time_spent,
                "totalActivities": len(progress_entries),
                "activities": activities
            }
            }),
            status_code=200,
            mimetype="application/json"
        )
        
    except Exception as e:
        logging.error(f'Error in GetTodayProgress: {str(e)}', exc_info=True)
        return func.HttpResponse(
            json.dumps({
                "success": False,
                "error": str(e)
            }),
            status_code=500,
            mimetype="application/json"
        )