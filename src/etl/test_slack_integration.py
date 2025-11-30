"""
Test script for Slack integration
Run locally to verify Slack API connection and user lookup
"""
import os
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError
from dotenv import load_dotenv

# Load environment variables from local.settings.json or .env
load_dotenv()

def test_slack_connection():
    """
    Test basic Slack API connection
    """
    print("\n" + "="*50)
    print("🧪 TESTING SLACK INTEGRATION")
    print("="*50)
    
    # Get token from environment
    slack_token = os.environ.get('SLACK_BOT_TOKEN')
    
    if not slack_token:
        print("❌ SLACK_BOT_TOKEN not found in environment variables!")
        print("   Add it to local.settings.json or .env file")
        return False
    
    print(f"✅ Token found: {slack_token[:20]}...")
    
    # Initialize Slack client
    slack_client = WebClient(token=slack_token)
    
    try:
        # Test auth
        response = slack_client.auth_test()
        print(f"\n✅ Successfully connected to Slack!")
        print(f"   Workspace: {response['team']}")
        print(f"   Bot Name: {response['user']}")
        return slack_client
        
    except SlackApiError as e:
        print(f"\n❌ Slack API Error: {e.response['error']}")
        return None


def test_find_user_by_email(slack_client, email):
    """
    Test finding Slack user by email
    """
    print(f"\n🔍 Searching for user with email: {email}")
    
    try:
        response = slack_client.users_lookupByEmail(email=email)
        
        user_id = response['user']['id']
        user_name = response['user']['real_name']
        user_display_name = response['user']['profile'].get('display_name', 'N/A')
        
        print(f"✅ Found user!")
        print(f"   Real Name: {user_name}")
        print(f"   Display Name: {user_display_name}")
        print(f"   Slack User ID: {user_id}")
        print(f"   Mention syntax: <@{user_id}>")
        
        return user_id
        
    except SlackApiError as e:
        if e.response['error'] == 'users_not_found':
            print(f"❌ User not found in Slack workspace")
            print(f"   Make sure {email} is a member of your Slack workspace")
        else:
            print(f"❌ Slack API Error: {e.response['error']}")
        return None
    except Exception as e:
        print(f"❌ Error: {str(e)}")
        return None


def test_send_message(slack_client):
    """
    Test sending a simple message to channel
    """
    channel_id = os.environ.get('SLACK_DAILY_REPORTS_CHANNEL_ID')
    
    if not channel_id:
        print("\n⚠️  SLACK_DAILY_REPORTS_CHANNEL_ID not found")
        print("   Skipping message send test")
        return
    
    print(f"\n📤 Testing message send to channel: {channel_id}")
    
    try:
        response = slack_client.chat_postMessage(
            channel=channel_id,
            text="🧪 Test message from AITut.Iverse",
            blocks=[
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": "🧪 *Test Message*\n\nThis is a test message from the AITut.Iverse daily report system!"
                    }
                },
                {
                    "type": "context",
                    "elements": [
                        {
                            "type": "mrkdwn",
                            "text": "If you see this, the integration is working! ✅"
                        }
                    ]
                }
            ]
        )
        
        print(f"✅ Message sent successfully!")
        print(f"   Message timestamp: {response['ts']}")
        
    except SlackApiError as e:
        print(f"❌ Failed to send message: {e.response['error']}")


if __name__ == "__main__":
    print("\n🚀 Starting Slack Integration Tests...\n")
    
    # Test 1: Connection
    slack_client = test_slack_connection()
    
    if not slack_client:
        print("\n❌ Connection test failed. Fix the token and try again.")
        exit(1)
    
    # Test 2: Find user by email
    # ⚠️ CHANGE THIS TO A REAL EMAIL FROM YOUR SLACK WORKSPACE
    test_email = "yuliiakuts@gmail.com"
    
    print(f"\n{'='*50}")
    print("TEST: Find User by Email")
    print(f"{'='*50}")
    user_id = test_find_user_by_email(slack_client, test_email)
    
    # Test 3: Send test message
    print(f"\n{'='*50}")
    print("TEST: Send Message to Channel")
    print(f"{'='*50}")
    test_send_message(slack_client)
    
    # Summary
    print(f"\n{'='*50}")
    print("✨ TEST SUMMARY")
    print(f"{'='*50}")
    print("✅ Slack connection: PASSED")
    print(f"{'✅' if user_id else '❌'} User lookup: {'PASSED' if user_id else 'FAILED'}")
    print("\n💡 Next steps:")
    print("   1. Add real test email above")
    print("   2. Make sure SLACK_DAILY_REPORTS_CHANNEL_ID is set")
    print("   3. Run this script again to verify everything works")
    print(f"\n{'='*50}\n")