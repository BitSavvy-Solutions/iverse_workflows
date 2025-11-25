import logging
import smtplib
import os
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import List

def send_email(
    recipients: List[str],
    subject: str,
    body_html: str,
    body_text: str
) -> bool:
    """
    Send email via SMTP.
    
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
        # Get SMTP configuration from environment
        smtp_host = os.environ.get('EMAIL_SMTP_HOST')
        smtp_port = int(os.environ.get('EMAIL_SMTP_PORT'))
        from_address = os.environ.get('EMAIL_FROM_ADDRESS')
        from_password = os.environ.get('EMAIL_FROM_PASSWORD')
        from_name = os.environ.get('EMAIL_FROM_NAME', 'AITUT Learning Platform')
        
        # Validate configuration
        if not all([smtp_host, from_address, from_password]):
            logging.error('Email configuration incomplete. Check environment variables.')
            return False
        
        # Create message
        message = MIMEMultipart('alternative')
        message['Subject'] = subject
        message['From'] = f'{from_name} <{from_address}>'
        message['To'] = ', '.join(recipients)
        
        # Attach both plain text and HTML versions
        part_text = MIMEText(body_text, 'plain')
        part_html = MIMEText(body_html, 'html')
        
        message.attach(part_text)
        message.attach(part_html)
        
        # Send email
        logging.info(f'Connecting to SMTP server: {smtp_host}:{smtp_port}')
        
        with smtplib.SMTP_SSL(smtp_host, smtp_port) as server:
            server.login(from_address, from_password)
            server.send_message(message)
        
        logging.info(f'✅ Email sent successfully to {len(recipients)} recipient(s)')
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