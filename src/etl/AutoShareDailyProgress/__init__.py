"""
Azure Function to automatically share daily progress to Slack
Runs on a schedule (e.g., every day at 10 PM)
Finds students who studied today but forgot to manually share their progress
"""
import logging
import azure.functions as func
from datetime import datetime, timedelta, timezone
from pymongo import MongoClient
import os
from shared.slack_service import send_daily_report_to_slack
from shared.github_service import get_material_title


def main(mytimer: func.TimerRequest) -> None:
    """
    Timer trigger to auto-share daily progress for students who forgot
    
    Schedule: 0 0 22 * * * (Every day at 10 PM UTC)
    
    Logic:
    1. For each circle with Slack integration
    2. For each student in that circle
    3. If student has progress today BUT hasn't shared to Slack
    4. Auto-share their progress
    """
    logging.info('AutoShareDailyProgress function triggered')
    
    if mytimer.past_due:
        logging.info('Timer is past due!')
    
    try:
        # Connect to database
        client = MongoClient(os.environ['COSMOS_CONNECTION_STRING'])
        user_db = client['userdb']
        course_db = client['coursedb']
        
        users_collection = user_db['users']
        circle_members_collection = user_db['circleMembers']
        circles_collection = user_db['circles']
        progress_collection = course_db['progress']
        
        # Get time range for today (last 24 hours)
        end_time = datetime.now(timezone.utc)
        start_time = end_time - timedelta(hours=24)
        
        logging.info(f'Processing progress from {start_time} to {end_time}')
        
        # Get all active circles with Slack integration
        circles = list(circles_collection.find({
            'isActive': True,
            'slackChannelId': {'$exists': True, '$ne': None}
        }))
        
        logging.info(f'Found {len(circles)} active circles with Slack integration')
        
        total_auto_shared = 0
        total_students_checked = 0
        
        # Process each circle
        for circle in circles:
            circle_id = circle.get('circleId')
            circle_name = circle.get('name', 'Unknown Circle')
            circle_slack_channel = circle.get('slackChannelId')
            
            logging.info(f'\n📍 Processing circle: {circle_name} ({circle_id})')
            logging.info(f'   Slack channel: {circle_slack_channel}')
            
            # Get all students in this circle
            circle_members = list(circle_members_collection.find({
                'circleId': circle_id,
                'role': 'student'
            }))
            
            logging.info(f'   Found {len(circle_members)} students in circle')
            
            # Process each student
            for member in circle_members:
                user_id = member.get('userId')
                total_students_checked += 1
                
                # Get user info
                user = users_collection.find_one({'userId': user_id})
                if not user:
                    logging.warning(f'   ⚠️ User not found: {user_id}')
                    continue
                
                user_name = (user.get('profile') or {}).get('name') or user.get('name', 'Student')
                user_email = user.get('email')
                
                if not user_email:
                    logging.warning(f'   ⚠️ No email for user: {user_name}')
                    continue
                
                # Check if student has progress today
                progress_entries = list(progress_collection.find({
                    'userId': user_id,
                    'createdAt': {
                        '$gte': start_time,
                        '$lte': end_time
                    }
                }))
                
                if len(progress_entries) == 0:
                    logging.info(f'   ⏭️ {user_name}: No progress today, skipping')
                    continue
                
                # Check if student already shared manually today
                already_shared = progress_collection.find_one({
                    'userId': user_id,
                    'createdAt': {
                        '$gte': start_time,
                        '$lte': end_time
                    },
                    'sharedToSlack': True
                })
                
                if already_shared:
                    logging.info(f'   ✅ {user_name}: Already shared manually, skipping')
                    continue
                
                # Student has progress but hasn't shared → Auto-share!
                logging.info(f'   🚀 {user_name}: Has progress but not shared, auto-sharing...')
                
                # Prepare report data (same logic as ShareProgressToSlack)
                success = auto_share_student_progress(
                    user_id=user_id,
                    user_name=user_name,
                    user_email=user_email,
                    progress_entries=progress_entries,
                    circle_slack_channel=circle_slack_channel,
                    progress_collection=progress_collection
                )
                
                if success:
                    total_auto_shared += 1
                    logging.info(f'   ✅ Auto-shared for {user_name}')
                else:
                    logging.error(f'   ❌ Failed to auto-share for {user_name}')
        
        client.close()
        
        logging.info(f'\n📊 SUMMARY:')
        logging.info(f'   Circles processed: {len(circles)}')
        logging.info(f'   Students checked: {total_students_checked}')
        logging.info(f'   Auto-shared: {total_auto_shared}')
        logging.info(f'✅ AutoShareDailyProgress completed successfully')
        
    except Exception as e:
        logging.error(f'❌ Error in AutoShareDailyProgress: {str(e)}', exc_info=True)
        raise


def auto_share_student_progress(
    user_id: str,
    user_name: str,
    user_email: str,
    progress_entries: list,
    circle_slack_channel: str,
    progress_collection
) -> bool:
    """
    Auto-share student's daily progress to Slack
    Same logic as manual ShareProgressToSlack but without user comment
    
    Returns:
        True if successful, False otherwise
    """
    try:
        # Group progress by material (same logic as ShareProgressToSlack)
        material_groups = {}
        
        for entry in progress_entries:
            chapter_id = entry.get('chapterId')
            material_id = entry.get('materialId')
            
            if not chapter_id or not material_id:
                continue
            
            material_key = f"{chapter_id}:{material_id}"
            
            if material_key not in material_groups:
                material_groups[material_key] = {
                    'chapterId': chapter_id,
                    'materialId': material_id,
                    'isCompleted': entry.get('isCompleted', False),
                    'timeSpentSeconds': 0,
                    'updateTexts': []
                }
            
            # Aggregate time
            material_groups[material_key]['timeSpentSeconds'] += entry.get('timeSpentSeconds', 0)
            
            # Track completion status (if any entry is completed → mark as completed)
            if entry.get('isCompleted', False):
                material_groups[material_key]['isCompleted'] = True
            
            # Collect update texts
            update_text = entry.get('updateText', '').strip()
            if update_text:
                material_groups[material_key]['updateTexts'].append(update_text)
        
        # Calculate totals
        total_time_seconds = 0
        completed_count = 0
        inprogress_count = 0
        completed_materials = []
        
        for material_key, material in material_groups.items():
            chapter_id = material['chapterId']
            material_id = material['materialId']
            is_completed = material['isCompleted']
            time_seconds = material['timeSpentSeconds']
            
            total_time_seconds += time_seconds
            
            if is_completed:
                completed_count += 1
            else:
                inprogress_count += 1
            
            # Get material title from GitHub
            material_title = get_material_title(chapter_id, material_id)
            display_text = material_title or f"{chapter_id} - {material_id}"
            
            # Format time
            time_minutes = time_seconds // 60
            if time_minutes > 0:
                time_str = f"({time_minutes} min)"
            else:
                time_str = ""
            
            # Format status emoji
            status_emoji = "✅" if is_completed else "🔄"
            
            # Format final text
            formatted_text = f"{status_emoji} {display_text} {time_str}".strip()
            
            # Add to list
            completed_materials.append({
                'updateText': formatted_text,
                'isCompleted': is_completed,
                'materialTitle': display_text
            })
        
        # Add auto-generated comment at the beginning
        completed_materials.insert(0, {
            'updateText': f"🤖 Auto-shared daily update",
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
        
        # Update database: mark as shared
        if success:
            progress_collection.update_many(
                {
                    'userId': user_id,
                    'createdAt': {
                        '$gte': datetime.now(timezone.utc) - timedelta(hours=24),
                        '$lte': datetime.now(timezone.utc)
                    }
                },
                {
                    '$set': {
                        'sharedToSlack': True,
                        'sharedAt': datetime.now(timezone.utc),
                        'sharedType': 'auto'  # To distinguish from manual shares
                    }
                }
            )
        
        return success
        
    except Exception as e:
        logging.error(f'Error in auto_share_student_progress: {str(e)}', exc_info=True)
        return False
