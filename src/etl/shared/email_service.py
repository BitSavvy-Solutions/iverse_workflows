import logging
import os
from typing import List, Tuple
from azure.communication.email import EmailClient
from concurrent.futures import ThreadPoolExecutor, as_completed

_cached_email_client = None

def get_email_client():
    """
    Helper function to get or create the EmailClient.
    This implements the 'Singleton' pattern to reuse the connection.
    """
    global _cached_email_client
    
    # If already created, return immediately
    if _cached_email_client:
        return _cached_email_client
        
    # If not, create it (Happens only once per Function App instance)
    connection_string = os.environ.get('COMMUNICATION_SERVICES_CONNECTION_STRING')
    
    if connection_string:
        try:
            _cached_email_client = EmailClient.from_connection_string(connection_string)
            return _cached_email_client
        except Exception as e:
            logging.error(f"Error initializing EmailClient: {e}")
            return None
    return None


def send_email(
    recipients: List[str],
    subject: str,
    body_html: str,
    body_text: str
) -> bool:
    """
    Send a SINGLE email to multiple recipients via Azure Communication Services.
    
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
        from_address = os.environ.get('EMAIL_FROM_ADDRESS')

        client = get_email_client()
        
        # Validate configuration
        if not client or not from_address:
            logging.error('Azure Communication Services configuration incomplete or Client failed to init.')
            return False
                
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
    

def send_email_to_list(
    recipients: List[str],
    subject: str,
    body_html: str,
    body_text: str,
    cc_email: str = None
) -> Tuple[int, int]:
    """
    Sends individual emails to a list of recipients in PARALLEL.
    
    Args:
        recipients: List of email addresses
        subject: Email subject line
        body_html: HTML version
        body_text: Plain text version
        
    Returns:
        Tuple containing (success_count, error_count)
    """
    
    if not recipients:
        return 0, 0

    # Get connection string once
    from_address = os.environ.get('EMAIL_FROM_ADDRESS')
    
    client = get_email_client()
    
    if not client or not from_address:
        logging.error('Azure Communication Services configuration incomplete or Client failed to init.')
        return 0, len(recipients)

    # Helper function to send ONE email
    def send_single(recipient_email):
        try:
            # Construct the recipients dictionary
            recipients_payload = {
                "to": [{"address": recipient_email}]
            }
            
            # <--- NEW LOGIC: Add CC if provided
            if cc_email:
                recipients_payload["cc"] = [{"address": cc_email}]

            message = {
                "senderAddress": from_address,
                "recipients": recipients_payload,
                "content": {
                    "subject": subject,
                    "plainText": body_text,
                    "html": body_html
                }
            }
            
            # Send and wait for result
            poller = client.begin_send(message)
            result = poller.result()
            return True
        except Exception as e:
            logging.error(f"Failed to send to {recipient_email}: {str(e)}")
            return False

    # --- PARALLEL EXECUTION START ---
    success_count = 0
    error_count = 0
    
    # We use a ThreadPool to send multiple emails at the same time.
    # max_workers=10, send 10 emails simultaneously.
    with ThreadPoolExecutor(max_workers=10) as executor:
        # Submit all tasks
        future_to_email = {executor.submit(send_single, email): email for email in recipients}
        
        # Process results as they finish
        for future in as_completed(future_to_email):
            email = future_to_email[future]
            try:
                is_success = future.result()
                if is_success:
                    success_count += 1
                else:
                    error_count += 1
            except Exception as exc:
                logging.error(f'{email} generated an exception: {exc}')
                error_count += 1
                
    logging.info(f"Batch complete. Success: {success_count}, Errors: {error_count}")
    return success_count, error_count


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