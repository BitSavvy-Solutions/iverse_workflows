import logging
import os
from typing import List
from azure.communication.email import EmailClient

def send_email(
    recipients: List[str],
    subject: str,
    body_html: str,
    body_text: str
) -> bool:
    """
    Send email via Azure Communication Services.
    
    Args:
        recipients: List of email addresses
        subject: Email subject line
        body_html: HTML version of email body
        body_text: Plain text version of email body
        
    Returns:
        True if successful, False otherwise
    """
    
    if not recipients or len(recipients) == 0:
        logging.warning('No recipients provided, skipping email')
        return False
    
    try:
        # Get Azure Communication Services connection string
        connection_string = os.environ.get('COMMUNICATION_SERVICES_CONNECTION_STRING')
        from_address = os.environ.get('EMAIL_FROM_ADDRESS')
        
        # Validate configuration
        if not all([connection_string, from_address]):
            logging.error('Azure Communication Services configuration incomplete.')
            return False
        
        # Create email client
        client = EmailClient.from_connection_string(connection_string) 
        
        # Build email message
        message = {
            "senderAddress": from_address,
            "recipients": {
                "to": [{"address": email} for email in recipients]
            },
            "content": {
                "subject": subject,
                "plainText": body_text,
                "html": body_html
            }
        }

        # Send email
        logging.info(f'Sending email via Azure Communication Services to {len(recipients)} recipient(s)')
        
        poller = client.begin_send(message)
        result = poller.result()

        # Handle if Azure returns a Dictionary OR an Object
        if isinstance(result, dict):
            # In the dict, the key is usually 'messageId' (camelCase)
            msg_id = result.get("messageId") 
        else:
            # In the object, the attribute is usually 'message_id' (snake_case)
            msg_id = getattr(result, "message_id", "Unknown ID")

        logging.info(f'✅ Email sent successfully. Message ID: {msg_id}')
        return True
            
    except Exception as error:
        logging.error(f'❌ Failed to send email: {str(error)}', exc_info=True)
        return False


def send_test_email(recipient: str) -> bool:
    """
    Send a test email to verify SMTP configuration.
    
    Args:
        recipient: Email address to send test to
        
    Returns:
        True if successful, False otherwise
    """
    
    subject = 'Test Email from AITUT Email Service'
    
    body_html = """
    <html>
        <body>
            <h2>Test Email</h2>
            <p>This is a test email from the AITUT Email Report Service.</p>
            <p>If you're seeing this, your SMTP configuration is working correctly! ✅</p>
        </body>
    </html>
    """
    
    body_text = """
    Test Email
    
    This is a test email from the AITUT Email Report Service.
    If you're seeing this, your SMTP configuration is working correctly!
    """
    
    return send_email([recipient], subject, body_html, body_text)