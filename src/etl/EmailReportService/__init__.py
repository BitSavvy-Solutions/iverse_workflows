import logging
import azure.functions as func
from datetime import datetime, timedelta, timezone
import os
import json
from pymongo import MongoClient
from typing import List, Dict, Any, Optional
from jinja2 import Environment, FileSystemLoader, select_autoescape

from shared.email_service import send_email_to_list
from shared.github_service import get_material_title, get_chapter_title

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

    client = None
    try:
        connection_string = os.environ['COSMOS_CONNECTION_STRING']
        client = MongoClient(connection_string)
        user_db = client['userdb']
        course_db = client['coursedb']

        # Parse Request
        req_body = {}
        try:
            req_body = req.get_json()
        except ValueError:
            pass
        
        report_date = req_body.get('report_date')
        circle_id_filter = req_body.get('circle_id')
        
        if not report_date:
            yesterday = datetime.now(timezone.utc) - timedelta(days=1)
            report_date = yesterday.strftime('%Y-%m-%d')
        
        # Fetch Reports
        reports = fetch_reports(course_db, user_db, report_date, circle_id_filter)
        
        if not reports:
            return func.HttpResponse(
                json.dumps({
                    "success": True, ""
                    "message": "No reports found"
                }), 
                status_code=200)
        
        emails_sent = 0
        errors = 0
        
        for report in reports:
            try:
                circle_id = report['circleId']
                
                # Get Members
                members_map = get_circle_members_data(user_db, circle_id)
                if not members_map:
                    continue

                # Prepare SHARED Data (Leaderboard, Course Updates, Circle Stats, titles)
                shared_context = prepare_shared_context(report, members_map)
                
                subject = f'Daily Progress Report: {circle_id} - {report["reportDate"]}'

                # --- SEND TO EACH MEMBER INDIVIDUALLY ---
                for user_id, member_data in members_map.items():
                    try:
                        email = member_data.get('email')
                        if not email: 
                            continue

                        # Check roles
                        roles = member_data.get('roles', [])
                        if isinstance(member_data.get('role'), str):
                            roles = [member_data.get('role')]
                        
                        # Determine View Type
                        is_mentor = 'mentor' in roles
                        
                        # Prepare Context
                        context = shared_context.copy()
                        context['recipient_first_name'] = member_data.get('first_name', 'User')
                        context['profile_pic'] = member_data.get('profile_pic')
                        context['is_mentor'] = is_mentor
                        
                        # If Student, inject personal stats
                        if not is_mentor:
                            context['personal_stats'] = get_personal_stats(report, user_id)

                        # Render & Send
                        html_content = render_html('daily_report.html', context)
                        
                        sent, err = send_email_to_list(
                            [email], 
                            subject, 
                            html_content, 
                            "Please enable HTML.",
                            cc_email="munk@iverse.space" if is_mentor else None
                            # cc_email="desireecapacia.dev@gmail.com" if is_mentor else None
                        )
                        emails_sent += sent
                        errors += err

                    except Exception as inner_e:
                        logging.error(f"Error sending to {email}: {inner_e}")
                        errors += 1
                    
            except Exception as e:
                logging.error(f'Error processing report {report.get("reportId")}: {str(e)}', exc_info=True)
                errors += 1
        
        duration = (datetime.now(timezone.utc) - start_time).total_seconds()
        response_data = {
            "success": True,
            "emails_sent": emails_sent,
            "errors": errors,
            "duration_seconds": round(duration, 2)
        }
        
        return func.HttpResponse(
            json.dumps(response_data), 
            status_code=200, 
            mimetype="application/json")
        
    except Exception as error:
        logging.error(f'Fatal error: {str(error)}', exc_info=True)
        return func.HttpResponse(
            json.dumps({
                "error": str(error)
            }), 
            status_code=500)
    finally:
        if client: 
            client.close()

# ==========================================
# DATA PREPARATION FUNCTIONS
# ==========================================

def _format_id(text):
    """Helper to format IDs nicely (e.g. ch01 -> Ch01)"""
    if not text: 
        return "Unknown"
    
    return text.replace('-', ' ').replace('_', ' ').title()

def prepare_shared_context(report: dict, members_map: dict) -> dict:
    summary = report.get('summary', {})
    student_progress = report.get('studentProgress', [])
    detailed_progress = report.get('detailedProgress', [])

    # Leaderboard
    sorted_students = sorted(student_progress, key=lambda x: x.get('totalTimeSpent', 0), reverse=True)
    leaderboard = []
    for s in sorted_students[:5]:
        uid = s.get('userId')
        user_info = members_map.get(uid, {})
        leaderboard.append({
            'name': user_info.get('name', 'Student'),
            'time': _format_time(s.get('totalTimeSpent', 0))
        })

    # Course Updates
    completed_items = [d for d in detailed_progress if d.get('isCompleted') is True]
    course_updates = []

    for item in completed_items[:5]:
        chapter_id = item.get('chapterId')
        material_id = item.get('materialId')
        
        # Fetch real chapter & material title from GitHub service
        chapter_name = get_chapter_title(chapter_id) or _format_id(chapter_id)
        material_name = get_material_title(chapter_id, material_id) or item.get('updateText')

        course_updates.append({
            'header': chapter_name,
            'task': material_name
        })

    # Circle Stats
    circle_stats = {
        "total_hours": round(summary.get('totalTimeSpentSeconds', 0) / 3600, 1),
        "materials_completed": summary.get('materialsCompleted', 0),
        "active_students": summary.get('activeStudents', 0),
        "total_students": summary.get('totalStudents', 0),
        "completion_rate": int(summary.get('completionRate', 0) * 100)
    }

    return {
        "circle_id": report.get('circleId'),
        "report_date": report.get('reportDate'),
        "generated_at": datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC'),
        "leaderboard": leaderboard,
        "course_updates": course_updates,
        "circle_stats": circle_stats
    }

def get_personal_stats(report: dict, user_id: str) -> dict:
    detailed_progress = report.get('detailedProgress', [])
    user_entries = [d for d in detailed_progress if d.get('userId') == user_id]
    
    total_seconds = sum(d.get('timeSpentSeconds', 0) for d in user_entries)
    completed_count = sum(1 for d in user_entries if d.get('isCompleted') is True)
    
    achievements = []
    active_chapters = set() # Set to hold unique chapter IDs found in today's work

    for d in user_entries:
        if d.get('isCompleted'):
            chapter_id = d.get('chapterId')
            material_id = d.get('materialId')

            if chapter_id:
                active_chapters.add(chapter_id)

            task_name = get_material_title(chapter_id, material_id) or d.get('updateText')
            achievements.append({
                'task': task_name,
                'time': _format_time(d.get('timeSpentSeconds', 0))
            })

    chapter_header = "your active courses"
    
    if active_chapters:
        # Get the first chapter found (or you could sort/prioritize)
        primary_chapter = list(active_chapters)[0]
        real_title = get_chapter_title(primary_chapter)

        if real_title:
            chapter_header = f"chapter: {real_title}"
        else:
            chapter_header = f"chapter {_format_id(primary_chapter)}"

    return {
        "time_spent": _format_time(total_seconds),
        "materials_count": completed_count,
        "achievements": achievements,
        "chapter_context": chapter_header
    }

def _format_time(seconds):
    if not seconds: return "0m"
    m, s = divmod(seconds, 60)
    h, m = divmod(m, 60)
    if h > 0: return f"{int(h)}h {int(m)}m"
    return f"{int(m)}m"

def render_html(template_name: str, data: dict) -> str:
    try:
        template_dir = os.path.join(os.path.dirname(__file__), 'templates')
        env = Environment(loader=FileSystemLoader(template_dir), autoescape=select_autoescape(['html', 'xml']))
       
        return env.get_template(template_name).render(**data)
    
    except Exception as e:
        logging.error(f"Template rendering error: {e}")
        raise

def fetch_reports(course_db, user_db, report_date: str, circle_id_filter: Optional[str] = None) -> List[Dict]:
    try:
        circle_query = {'emailUpdates': True}
        if circle_id_filter: 
            circle_query['circleId'] = circle_id_filter

        eligible_ids = [d['circleId'] for d in user_db['circles'].find(circle_query)]

        if not eligible_ids: 
            return []
        
        query = {
            'reportDate': report_date, 
            'circleId': {'$in': eligible_ids}
        }
        
        return list(course_db['reports'].find(query))
    
    except Exception as e:
        logging.error(f"DB Error: {e}")
        raise

def get_circle_members_data(user_db, circle_id: str) -> Dict[str, Dict]:
    try:
        memberships = list(user_db['circleMembers'].find({'circleId': circle_id}))
        if not memberships: 
            return {}
        
        role_map = {m['userId']: m.get('role', 'student') for m in memberships}
        user_ids = list(role_map.keys())
        users = list(user_db['users'].find({'userId': {'$in': user_ids}}))
        
        results = {}
        for user in users:
            uid = user.get('userId')
            raw_role = role_map.get(uid, 'student')
            
            roles_list = []
            if isinstance(raw_role, list):
                roles_list = [r.lower() for r in raw_role]
            elif isinstance(raw_role, str):
                roles_list = [raw_role.lower()]

            profile = user.get('profile', {})
            full_name = (profile.get('name') or user.get('name') or user.get('email', '').split('@')[0])
            
            # --- NEW: Extract First Name ---
            first_name = full_name.split()[0].title() if full_name else "User"
            
            # --- NEW: Get Profile Pic ---
            profile_pic = user.get('profilePictureUrl')

            results[uid] = {
                'email': user.get('email'),
                'roles': roles_list,
                'name': full_name,
                'first_name': first_name,
                'profile_pic': profile_pic
            }
        return results
    except Exception as e:
        logging.error(f"Error fetching members: {e}")
        return {}