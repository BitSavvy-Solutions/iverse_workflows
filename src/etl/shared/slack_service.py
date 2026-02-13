"""
Slack integration service for sending daily reports
"""
import logging
import os
from typing import Optional
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError
import re


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
    report_date: str,
    slack_channel_id: str = None
) -> bool:
    """
    Send daily progress report to Slack channel
    
    Args:
        user_name: Student's display name
        user_email: Student's email (to find Slack ID)
        report_data: Dictionary with progress data
        report_date: Date string (YYYY-MM-DD)
        slack_channel_id: Circle-specific Slack channel ID (optional, falls back to env)
        
    Returns:
        True if successful, False otherwise
    """
    try:
        slack_client = WebClient(token=os.environ['SLACK_BOT_TOKEN'])
        channel_id = slack_channel_id or os.environ.get('SLACK_DAILY_REPORTS_CHANNEL_ID')
        
        if not channel_id:
            logging.error('No Slack channel ID provided')
            return False
        
        logging.info(f'Sending report to Slack channel: {channel_id}')
        
        # Try to find user's Slack ID
        slack_user_id = get_slack_user_id_by_email(user_email)
        
        # Format time spent
        total_seconds = report_data.get('totalTimeSpent', 0)
        total_minutes = total_seconds // 60
        hours = total_minutes // 60
        minutes = total_minutes % 60
        time_str = f"{hours}h {minutes}m" if hours > 0 else f"{minutes}m"
        
        # Format completed materials (WITHOUT emojis - we'll add them in sections)
        materials_text = ""
        completed_materials = report_data.get('completedMaterials', [])

        # Skip the first item if it's the user comment (starts with 💭)
        materials_to_show = [m for m in completed_materials if not m.get('updateText', '').startswith('💭')]

        for material in materials_to_show[:10]:  # Show max 10 items
            update_text = material.get('updateText', 'Progress update')
            materials_text += f"{update_text}\n"

        if len(materials_to_show) > 10:
            materials_text += f"_...and {len(materials_to_show) - 10} more activities_\n"

        if not materials_text:
            materials_text = "_No activities recorded today_"

        # Extract user comments (the one with 💭 prefix)
        user_comment_raw = next((m.get('updateText', '').replace('💭 ', '') for m in completed_materials if m.get('updateText', '').startswith('💭')), '')

        # Parse structured comments using regex
        what_i_did = ""
        what_i_will_do = ""
        blockers = ""

        if user_comment_raw:
            logging.info(f"🔍 DEBUG - Raw user comment received:\n{user_comment_raw}")
            
            # Match "📝 *Additional notes:* content"
            match_did = re.search(r'\*Additional notes:\*\s*(.+?)(?=\*Tomorrow|🚧|$)', user_comment_raw, re.DOTALL | re.IGNORECASE)
            if match_did:
                what_i_did = match_did.group(1).strip()
                logging.info(f"✅ Found 'What I did': {what_i_did[:100]}")
            else:
                logging.warning("❌ Could not parse 'What I did' section")
            
            # Match "*Tomorrow's plans:* content" (может быть с 📅 или без)
            match_will = re.search(r'(?:📅\s*)?\*Tomorrow\'?s plans:\*\s*(.+?)(?=🚧|$)', user_comment_raw, re.DOTALL | re.IGNORECASE)
            if match_will:
                what_i_will_do = match_will.group(1).strip()
                logging.info(f"✅ Found 'Tomorrow': {what_i_will_do[:100]}")
            else:
                logging.warning("❌ Could not parse 'Tomorrow' section")
            
            # Match "*Blockers:* content" (может быть с 🚧 или без)
            match_blockers = re.search(r'(?:🚧\s*)?\*Blockers:\*\s*(.+?)$', user_comment_raw, re.DOTALL | re.IGNORECASE)
            if match_blockers:
                blockers = match_blockers.group(1).strip()
                logging.info(f"✅ Found 'Blockers': {blockers[:100]}")
            else:
                logging.warning("❌ Could not parse 'Blockers' section")

        logging.info(f"📝 Parsed comments:")
        logging.info(f"   - What I did: {len(what_i_did)} chars - '{what_i_did[:80] if what_i_did else '(empty)'}'")
        logging.info(f"   - Tomorrow: {len(what_i_will_do)} chars - '{what_i_will_do[:80] if what_i_will_do else '(empty)'}'")
        logging.info(f"   - Blockers: {len(blockers)} chars - '{blockers[:80] if blockers else '(empty)'}'")
        logging.info(f"   - Raw comment: {user_comment_raw[:200] if user_comment_raw else '(no comment)'}")

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
                    "text": f"{user_mention}! *Here's your progress for {report_date}:*"
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
            }
        ]

        # Add "What did I do today?" section
        if what_i_did:
            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*📝 What did I do today?*\n{what_i_did}"
                }
            })

        # Add "What will I do tomorrow?" section
        if what_i_will_do:
            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*📅 What will I do tomorrow?*\n{what_i_will_do}"
                }
            })

        # Add "Any Blockers?" section
        if blockers:
            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*🚧 Any Blockers?*\n{blockers}"
                }
            })

        # Footer
        blocks.append({
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": "🌱 Keep growing! | AITut.Iverse"
                }
            ]
        })

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