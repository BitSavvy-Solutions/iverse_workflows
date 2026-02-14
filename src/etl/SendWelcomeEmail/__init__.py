"""
Azure Function to send welcome emails to Namibia learners
Personalized with student names
"""
import logging
import azure.functions as func
import json
from azure.communication.email import EmailClient
import os
from typing import List, Dict


def main(req: func.HttpRequest) -> func.HttpResponse:
    """
    HTTP trigger to send welcome emails to students
    
    Request Body (JSON):
    {
        "recipients": [
            {"name": "Elizabeth Nlooto", "email": "enlooto@gmail.com"},
            {"name": "Yuliia Kuts", "email": "yuliiakuts@gmail.com"}
        ],
        "test_mode": true  // If true, only sends to test recipients
    }
    """
    logging.info('SendWelcomeEmail function triggered')
    
    try:
        # Parse request body
        req_body = req.get_json()
        
        recipients = req_body.get('recipients', [])
        test_mode = req_body.get('test_mode', True)
        
        if not recipients or len(recipients) == 0:
            return func.HttpResponse(
                json.dumps({
                    "success": False,
                    "error": "No recipients provided"
                }),
                status_code=400,
                mimetype="application/json"
            )
        
        logging.info(f'Sending welcome emails to {len(recipients)} recipients (test_mode: {test_mode})')
        
        # Get Azure Communication Services credentials
        connection_string = os.environ.get('COMMUNICATION_SERVICES_CONNECTION_STRING')
        from_address = os.environ.get('EMAIL_FROM_ADDRESS', 'aitut@iverse.space')
        
        if not connection_string:
            return func.HttpResponse(
                json.dumps({
                    "success": False,
                    "error": "COMMUNICATION_SERVICES_CONNECTION_STRING not configured"
                }),
                status_code=500,
                mimetype="application/json"
            )
        
        # Create email client
        client = EmailClient.from_connection_string(connection_string)
        
        # Send emails
        results = []
        success_count = 0
        error_count = 0
        
        for recipient in recipients:
            name = recipient.get('name', 'Student')
            email = recipient.get('email')
            
            if not email:
                logging.warning(f'Skipping recipient with no email: {name}')
                continue
            
            try:
                # Generate personalized email
                html_body = generate_welcome_email_html(name)
                plain_text = generate_welcome_email_text(name)
                
                # Build message
                message = {
                    "senderAddress": from_address,
                    "recipients": {
                        "to": [{"address": email}]
                    },
                    "content": {
                        "subject": "Welcome to AI Tutor - Namibia Learning Community 🌸",
                        "plainText": plain_text,
                        "html": html_body
                    }
                }
                
                # Send email
                if not test_mode:
                    poller = client.begin_send(message)
                    result = poller.result()
                    message_id = result.message_id if hasattr(result, 'message_id') else result.get('messageId', 'unknown')
                else:
                    message_id = f"test-{email}"
                
                logging.info(f'✅ Email sent to {name} ({email}): {message_id}')
                success_count += 1
                
                results.append({
                    "name": name,
                    "email": email,
                    "status": "sent",
                    "message_id": message_id
                })
                
            except Exception as e:
                logging.error(f'❌ Failed to send email to {name} ({email}): {str(e)}')
                error_count += 1
                
                results.append({
                    "name": name,
                    "email": email,
                    "status": "failed",
                    "error": str(e)
                })
        
        # Return summary
        return func.HttpResponse(
            json.dumps({
                "success": True,
                "test_mode": test_mode,
                "total_recipients": len(recipients),
                "sent": success_count,
                "failed": error_count,
                "results": results
            }, indent=2),
            status_code=200,
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
        logging.error(f'Error in SendWelcomeEmail: {str(e)}', exc_info=True)
        return func.HttpResponse(
            json.dumps({
                "success": False,
                "error": str(e)
            }),
            status_code=500,
            mimetype="application/json"
        )


def generate_welcome_email_html(student_name: str) -> str:
    """Generate personalized HTML email for student"""
    
    # Extract first name for greeting
    first_name = student_name.split()[0] if student_name else "there"
    
    # Load template from file
    import os
    from pathlib import Path
    
    template_path = Path(__file__).parent / 'template.html'
    
    with open(template_path, 'r', encoding='utf-8') as f:
        html_template = f.read()
    
    # Replace placeholder with actual first name
    html = html_template.replace('{{FIRST_NAME}}', first_name)
    
    return html


def generate_welcome_email_text(student_name: str) -> str:
    """Generate personalized plain text email for student"""
    
    first_name = student_name.split()[0] if student_name else "there"
    
    return f"""Hi {first_name},

A warm welcome to our Namibia learning community!

We're excited to share some updates to the AI Tutor platform that are designed to support you better throughout your learning journey.

What's new on the platform?

1. Track Your Learning Progress
You can now easily track your progress as you move through lessons and activities. This will help you:
• See which lessons you've already completed
• Understand what to focus on next
• Stay motivated by clearly seeing your learning journey over time

2. Learners Circle (Namibia Cohort)
We've introduced a dedicated Learners Circle where:
• All Namibia learners are added in one place
• You can see the progress of your fellow learners
• Learning feels more connected and collaborative
• It becomes easier for our team to support and guide you

This space is meant to help you feel part of a learning community, even while learning remotely.

Please log in to explore these updates: https://aitut.iverse.space/dashboard

💡 Need help getting started?
We're here to support you every step of the way:
📝 Submit a support request: https://feedback.iverse.space/?source=email-welcome
💬 Reach out to your mentor in our Slack community

We're really excited to have you onboard and look forward to supporting you throughout this learning journey.

Warm regards,
The AI Tutor Team

🌱 Empowering your learning journey | AITut.Iverse
"""