"""
Slack integration service for sending daily reports
"""
import logging
import os
from typing import Optional
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError


def get_slack_user_id_by_email(email: str) -> Optional[str]:
    """
    Find Slack user ID by email address
    
    Args:
        email: User's email address
        
    Returns:
        Slack User ID (e.g., 'U09100PMZED') or None if not found
    """
    try:
        slack_client = WebClient(token=os.environ['SLACK_BOT_TOKEN'])
        response = slack_client.users_lookupByEmail(email=email)
        
        user_id = response['user']['id']
        user_name = response['user']['real_name']
        
        logging.info(f"Found Slack user: {user_name} ({user_id}) for email {email}")
        return user_id
        
    except SlackApiError as e:
        if e.response['error'] == 'users_not_found':
            logging.warning(f"User not found in Slack workspace: {email}")
        else:
            logging.error(f"Slack API error looking up user: {e.response['error']}")
        return None
    except Exception as e:
        logging.error(f"Error finding Slack user by email: {str(e)}")
        return None


def send_daily_report_to_slack(
    user_name: str,
    user_email: str,
    report_data: dict,
    report_date: str
) -> bool:
    """
    Send daily progress report to Slack channel
    
    Args:
        user_name: Student's display name
        user_email: Student's email (to find Slack ID)
        report_data: Dictionary with progress data
        report_date: Date string (YYYY-MM-DD)
        
    Returns:
        True if successful, False otherwise
    """
    try:
        slack_client = WebClient(token=os.environ['SLACK_BOT_TOKEN'])
        channel_id = os.environ['SLACK_DAILY_REPORTS_CHANNEL_ID']
        
        # Try to find user's Slack ID
        slack_user_id = get_slack_user_id_by_email(user_email)
        
        # Format time spent
        total_seconds = report_data.get('totalTimeSpent', 0)
        total_minutes = total_seconds // 60
        hours = total_minutes // 60
        minutes = total_minutes % 60
        time_str = f"{hours}h {minutes}m" if hours > 0 else f"{minutes}m"
        
        # Format completed materials
        materials_text = ""
        completed_materials = report_data.get('completedMaterials', [])
        
        for material in completed_materials[:5]:  # Show max 5 items
            
            update_text = material.get('updateText', 'Progress update')
            materials_text += f"{update_text}\n"
        
        if len(completed_materials) > 5:
            materials_text += f"_...and {len(completed_materials) - 5} more activities_\n"
        
        if not materials_text:
            materials_text = "_No activities recorded today_"
        
        # Create user mention if found
        user_mention = f"<@{slack_user_id}>" if slack_user_id else user_name

        # Build message blocks
        blocks = [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",  
                    "text": f"📚 Daily Report - {user_name}", 
                    "emoji": True
                }
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"Hey {user_mention}! 👋\n\n*Here's your progress for {report_date}:*"  # ← @mention здесь
                }
            },
            {
                "type": "section",
                "fields": [
                    {
                        "type": "mrkdwn",
                        "text": f"*Time Spent:*\n⏱️ {time_str}"
                    },
                    {
                        "type": "mrkdwn",
                        "text": f"*Completed:*\n✅ {report_data.get('completedCount', 0)} materials"
                    },
                    {
                        "type": "mrkdwn",
                        "text": f"*In Progress:*\n⏳ {report_data.get('inprogressCount', 0)} materials"
                    }
                ]
            },
            {
                "type": "divider"
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*Today's Activities:*\n{materials_text}"
                }
            },
            {
                "type": "context",
                "elements": [
                    {
                        "type": "mrkdwn",
                        "text": "🌱 Keep growing! | AITut.Iverse"
                    }
                ]
            }
        ]
        
        # Send message
        response = slack_client.chat_postMessage(
            channel=channel_id,
            text=f"Daily Report: {user_name}",  # Fallback text
            blocks=blocks
        )
        
        logging.info(f"✅ Daily report sent to Slack for {user_name} (ts: {response['ts']})")
        return True
        
    except SlackApiError as e:
        logging.error(f"Slack API error sending report: {e.response['error']}")
        return False
    except Exception as e:
        logging.error(f"Error sending report to Slack: {str(e)}", exc_info=True)
        return False