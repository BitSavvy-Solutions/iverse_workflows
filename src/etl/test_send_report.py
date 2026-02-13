"""
Test script to send a sample daily report to Slack
This will show you how the final message will look in the channel
"""
import os
from dotenv import load_dotenv
from shared.slack_service import send_daily_report_to_slack

# Load environment variables
load_dotenv()

# Sample report data (similar to what DailyReportGenerator creates)
sample_report_data = {
    'totalTimeSpent': 9000,  # 2.5 hours in seconds
    'completedCount': 3,
    'completedMaterials': [
        {
            'updateText': 'Completed async/await exercises',
            'isCompleted': True
        },
        {
            'updateText': 'Studied Promise fundamentals',
            'isCompleted': True
        },
        {
            'updateText': 'Working on REST API project',
            'isCompleted': False
        },
        {
            'updateText': 'Reviewed JavaScript callbacks',
            'isCompleted': True
        }
    ]
}

if __name__ == "__main__":
    print("\n📤 Sending test daily report to Slack...\n")
    
    # Send test report
    success = send_daily_report_to_slack(
        user_name="Yuliia Kuts",  # Your name
        user_email="yuliiakuts@gmail.com",  # Your email (to test @mention)
        report_data=sample_report_data,
        report_date="2025-11-27"
    )
    
    if success:
        print("\n✅ Test report sent successfully!")
        print("📱 Check your Slack channel #daily-student-reports to see how it looks!")
    else:
        print("\n❌ Failed to send test report. Check the logs above.")