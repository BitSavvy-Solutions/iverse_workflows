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
from shared.github_service import get_material_title


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
        
        # ========================================
        # GROUP BY MATERIAL - Aggregate multiple entries
        # ========================================

        # Group entries by materialId
        material_groups = {}

        for progress in progress_entries:
            chapter_id = progress.get('chapterId', '')
            material_id = progress.get('materialId', '')
            key = f"{chapter_id}/{material_id}"
            
            if key not in material_groups:
                material_groups[key] = {
                    'chapterId': chapter_id,
                    'materialId': material_id,
                    'progressId': progress.get('progressId'),  # Use first progressId
                    'timeSpent': 0,
                    'completed': False,
                    'updateTexts': [],
                    'createdAt': progress.get('createdAt')
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
            
            # Keep the latest timestamp
            current_created = progress.get('createdAt')
            if current_created and current_created > material_groups[key]['createdAt']:
                material_groups[key]['createdAt'] = current_created

        logging.info(f'Grouped into {len(material_groups)} unique materials')
        
        # Calculate statistics from grouped data
        modules_completed = sum(1 for m in material_groups.values() if m['completed'])
        modules_in_progress = sum(1 for m in material_groups.values() if not m['completed'])
        total_time_spent = sum(m['timeSpent'] for m in material_groups.values())
        
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

        # Sort by creation time (most recent first)
        sorted_materials = sorted(
            material_groups.values(),
            key=lambda m: _to_datetime(m.get('createdAt')) or datetime.fromtimestamp(0, tz=timezone.utc),
            reverse=True
        )

        # Format activities for frontend
        activities = []
        for material in sorted_materials:
            chapter_id = material['chapterId']
            material_id = material['materialId']
            
            # Get material title from GitHub
            material_title = get_material_title(chapter_id, material_id)
            
            # Create readable title
            if material_title:
                title = material_title
            else:
                title = f"{chapter_id}/{material_id}"
            
            # Combine all update texts
            combined_update_text = ' • '.join(material['updateTexts']) if material['updateTexts'] else 'Progress update'
            
            # Normalize createdAt
            created_at_val = material.get('createdAt')
            created_at_dt = _to_datetime(created_at_val)
            created_at_serializable = created_at_dt.isoformat() if created_at_dt else created_at_val

            activities.append({
                'id': material['progressId'],
                'title': title,
                'completed': material['completed'],
                'chapterId': chapter_id,
                'materialId': material_id,
                'timeSpent': material['timeSpent'],
                'createdAt': created_at_serializable,
                'updateText': combined_update_text
            })

        client.close()
        
        return func.HttpResponse(
            json.dumps({
                "success": True,
                "data": {
                    "modulesCompleted": modules_completed,
                    "modulesInProgress": modules_in_progress,
                    "timeSpent": total_time_spent,
                    "totalActivities": len(material_groups),  # ← Теперь это количество уникальных материалов
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