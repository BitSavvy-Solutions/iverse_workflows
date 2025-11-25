import logging
import azure.functions as func
from datetime import datetime, timedelta, timezone
import os, json
from pymongo import MongoClient
from typing import List, Dict, Any, Optional

def main(req: func.HttpRequest) -> func.HttpResponse:
    """
    HTTP trigger function for manual/scheduled report generation.
    Accepts optional parameters for flexible testing.
    
    Request Body (JSON):
    {
        "dataset_day": "2025-11-11",  // Optional: specific date (YYYY-MM-DD)
        "circle_id": "circle123"      // Optional: specific circle ID
    }
    """
    start_time = datetime.now(timezone.utc)
    logging.info(f'Report Generation Started (HTTP Trigger): {start_time.isoformat()}')

    try:
        # Parse request parameters
        req_body = {}
        try:
            req_body = req.get_json()
        except ValueError:
            pass  # No body or invalid JSON, use defaults
        
        dataset_day_str = req_body.get('dataset_day')
        circle_id_filter = req_body.get('circle_id')
        
        # Parse dataset_day or default to yesterday
        if dataset_day_str:
            try:
                end_time = datetime.fromisoformat(dataset_day_str).replace(tzinfo=timezone.utc)
                # Set to end of that day
                end_time = end_time.replace(hour=23, minute=59, second=59)
            except ValueError:
                return func.HttpResponse(
                    json.dumps({"success": False, "error": "Invalid dataset_day format. Use YYYY-MM-DD"}),
                    status_code=400,
                    mimetype="application/json"
                )
        else:
            end_time = datetime.now(timezone.utc)
        
        start_time_24h = end_time - timedelta(hours=24)
        report_date = end_time.strftime('%Y-%m-%d')
        
        logging.info(f'Parameters: dataset_day={report_date}, circle_id={circle_id_filter or "all"}')
        
        # Run ETL pipeline
        result = run_etl_pipeline(
            start_time_24h=start_time_24h,
            end_time=end_time,
            report_date=report_date,
            circle_id_filter=circle_id_filter
        )
        
        # Return success response
        duration = (datetime.now(timezone.utc) - start_time).total_seconds()
        response_data = {
            "success": True,
            "message": "Report generation completed",
            "dataset_day": report_date,
            "circle_id_filter": circle_id_filter,
            "reports_generated": result['success_count'],
            "errors": result['error_count'],
            "duration_seconds": round(duration, 2)
        }
        
        logging.info(f'HTTP Response: {json.dumps(response_data)}')
        
        return func.HttpResponse(
            json.dumps(response_data, default=str),
            status_code=200,
            mimetype="application/json"
        )
        
    except Exception as error:
        logging.error(f'Fatal error in report generation: {str(error)}', exc_info=True)
        return func.HttpResponse(
            json.dumps({
                "success": False,
                "error": str(error)
            }),
            status_code=500,
            mimetype="application/json"
        )

def run_etl_pipeline(
    start_time_24h: datetime,
    end_time: datetime,
    report_date: str,
    circle_id_filter: Optional[str] = None
) -> Dict[str, Any]:
    """
    Core ETL pipeline logic.
    Extracted for reusability and testing.
    
    Args:
        start_time_24h: Start of report period
        end_time: End of report period
        report_date: Date string (YYYY-MM-DD)
        circle_id_filter: Optional specific circle ID to process
        
    Returns:
        Dictionary with success_count and error_count
    """
    client = None

    try:
        # ========================================
        # 1. SETUP - Connect to Cosmos DB
        # ========================================
        connection_string = os.environ['COSMOS_CONNECTION_STRING']
        client = MongoClient(connection_string)
        logging.info('Connected to Cosmos DB')

        user_db = client['userdb']
        course_db = client['coursedb']
        
        circles_collection = user_db['circles']
        circle_members_collection = user_db['circleMembers']  # NEW: For memberships
        progress_collection = course_db['progress']
        reports_collection = course_db['reports']

        # Create indexes if they don't exist (idempotent operation)
        ensure_indexes(reports_collection)

        # ========================================
        # 2. EXTRACT - Fetch Circles and Progress Data
        # ========================================
        
        # Get circles to process (filtered or all)
        if circle_id_filter:
            unique_circle_ids = [circle_id_filter]
            logging.info(f'Processing specific circle: {circle_id_filter}')
        else:
            unique_circle_ids = circles_collection.distinct('circleId')
            logging.info(f'Found {len(unique_circle_ids)} circles to process')

        if len(unique_circle_ids) == 0:
            logging.info('No circles found. Exiting.')
            return {'success_count': 0, 'error_count': 0}

        logging.info(f'Report period: {start_time_24h.isoformat()} to {end_time.isoformat()}')

        # ========================================
        # 3. PROCESS - Generate Report for Each Circle
        # ========================================
        
        reports = []
        success_count = 0
        error_count = 0

        for circle_id in unique_circle_ids:
            try:
                # Get circle info from first membership document
                circle_doc = circles_collection.find_one({'circleId': circle_id})
                if not circle_doc:
                    logging.warning(f'Circle {circle_id} not found in database. Skipping.')
                    error_count += 1
                    continue

                logging.info(f'INFO - Processing circle: {circle_id}')

                # Query memberships for this circle in circleMembers collection
                memberships = list(circle_members_collection.find({
                    'circleId': circle_id,
                    'role': 'mentee'
                }))
                student_ids = [m.get('userId') for m in memberships]

                if len(student_ids) == 0:
                    logging.info(f'Circle {circle_id} has no students. Skipping.')
                    continue

                # Fetch progress data for these students
                progress_query = {
                    'userId': {'$in': student_ids},
                    'submittedAt': {'$gte': start_time_24h, '$lte': end_time}
                }
                progress_data = list(progress_collection.find(progress_query))

                logging.info(f'Found {len(progress_data)} progress entries for circle {circle_id}')

                # ========================================
                # 4. TRANSFORM - Aggregate and Structure Data
                # ========================================
                
                report = generate_report(
                    circle_id,
                    len(memberships),  # Total students count
                    progress_data, 
                    report_date, 
                    start_time_24h, 
                    end_time
                )
                reports.append(report)
                success_count += 1

            except Exception as error:
                error_count += 1
                logging.error(f'Error processing circle {circle_id}: {str(error)}', exc_info=True)
                # Continue processing other circles

        # ========================================
        # 5. LOAD - Save Reports to Database
        # ========================================
        
        if len(reports) > 0:
            result = reports_collection.insert_many(reports)
            logging.info(f'Successfully saved {len(result.inserted_ids)} reports to database')
        else:
            logging.info('No reports generated')

        # ========================================
        # 6. SUMMARY
        # ========================================
        
        logging.info('========================================')
        logging.info('Daily Report Generation Complete')
        logging.info(f'Total circles processed: {len(unique_circle_ids)}')
        logging.info(f'Reports generated: {success_count}')
        logging.info(f'Errors: {error_count}')
        logging.info('========================================')

        return {'success_count': success_count, 'error_count': error_count}

    except Exception as error:
        logging.error(f'Fatal error in report generation: {str(error)}')
        raise  # Re-throw to mark function execution as failed

    finally:
        if client:
            client.close()
            logging.info('Database connection closed')


# ========================================
# HELPER FUNCTIONS
# ========================================

def generate_report(
    circle_id: str,
    total_students: int,
    progress_data: List[Dict[str, Any]], 
    report_date: str,
    start_time: datetime,
    end_time: datetime
) -> Dict[str, Any]:
    """
    Generate report structure with aggregated data
    
    Args:
        circle_id: Circle ID
        total_students: Total student count
        progress_data: List of progress entries
        report_date: Date string (YYYY-MM-DD)
        start_time: Report period start
        end_time: Report period end
        
    Returns:
        Complete report document ready to be saved
    """
    
    # Get unique active students
    active_student_ids = set(p.get('userId') for p in progress_data)
    
    # Calculate summary metrics
    materials_completed = sum(1 for p in progress_data if p.get('isCompleted', False))
    total_time_spent = sum(p.get('timeSpentSeconds', 0) for p in progress_data)
    avg_time_per_student = round(total_time_spent / len(active_student_ids)) if len(active_student_ids) > 0 else 0

    # Group progress by course
    course_groups = {}
    for p in progress_data:
        course_id = p.get('courseId')
        if course_id not in course_groups:
            course_groups[course_id] = {
                'courseId': course_id,
                'activeStudents': set(),
                'materialsCompleted': 0,
                'totalTimeSpent': 0
            }
        course_groups[course_id]['activeStudents'].add(p.get('userId'))
        if p.get('isCompleted', False):
            course_groups[course_id]['materialsCompleted'] += 1
        course_groups[course_id]['totalTimeSpent'] += p.get('timeSpentSeconds', 0)

    progress_by_course_summary = [
        {
            'courseId': g['courseId'],
            'activeStudents': len(g['activeStudents']),
            'materialsCompleted': g['materialsCompleted'],
            'totalTimeSpent': g['totalTimeSpent']
        }
        for g in course_groups.values()
    ]

    # Group progress by student
    student_groups = {}
    for p in progress_data:
        user_id = p.get('userId')
        if user_id not in student_groups:
            student_groups[user_id] = {
                'userId': user_id,
                'progressEntries': 0,
                'materialsCompleted': 0,
                'totalTimeSpent': 0,
                'latestUpdate': None,
                'latestTimestamp': None
            }
        student_groups[user_id]['progressEntries'] += 1
        if p.get('isCompleted', False):
            student_groups[user_id]['materialsCompleted'] += 1
        student_groups[user_id]['totalTimeSpent'] += p.get('timeSpentSeconds', 0)
        
        # Track latest update
        submitted_at = p.get('submittedAt')
        if (not student_groups[user_id]['latestTimestamp'] or 
            submitted_at > student_groups[user_id]['latestTimestamp']):
            student_groups[user_id]['latestUpdate'] = p.get('updateText')
            student_groups[user_id]['latestTimestamp'] = submitted_at

    student_progress_summary = [
        {
            'userId': s['userId'],
            'progressEntries': s['progressEntries'],
            'materialsCompleted': s['materialsCompleted'],
            'totalTimeSpent': s['totalTimeSpent'],
            'latestUpdate': s['latestUpdate']
        }
        for s in student_groups.values()
    ]

    # Get total students in circle (for completion rate calculation)
    completion_rate = round(len(active_student_ids) / total_students, 2) if total_students > 0 else 0

    # Build final report document
    return {
        'reportId': f'{circle_id}_{report_date}',
        'circleId': circle_id,
        'reportDate': report_date,
        'reportPeriod': {
            'startTime': start_time.isoformat(),
            'endTime': end_time.isoformat()
        },
        'generatedAt': datetime.now(timezone.utc).isoformat(),
        'summary': {
            'totalStudents': total_students,
            'activeStudents': len(active_student_ids),
            'totalProgressEntries': len(progress_data),
            'materialsCompleted': materials_completed,
            'totalTimeSpentSeconds': total_time_spent,
            'averageTimePerStudent': avg_time_per_student,
            'completionRate': completion_rate
        },
        'progressByCourse': progress_by_course_summary,
        'studentProgress': student_progress_summary,
        'detailedProgress': progress_data  # Full data for reference
    }


def ensure_indexes(reports_collection) -> None:
    """
    Ensure database indexes exist for efficient querying
    
    Args:
        reports_collection: MongoDB collection object
    """
    try:
        reports_collection.create_index([('circleId', 1), ('reportDate', -1)])
        reports_collection.create_index([('reportDate', -1)])
        logging.info('Database indexes ensured')
    except Exception as error:
        logging.warning(f'Index creation warning: {str(error)}')
        # Non-fatal - continue execution