import logging
import azure.functions as func
import requests
import os

def main(myTimer: func.TimerRequest) -> None:
    """
    Timer trigger that calls the EmailReportService HTTP endpoint daily at 08:00 UTC.
    This is just a thin wrapper - all logic is in the HTTP function.
    """
    logging.info('Timer triggered - calling EmailReportService endpoint')
    
    try:
        # Get function URL (local or deployed)
        function_url = os.environ.get('FUNCTION_APP_URL', 'http://localhost:7071')
        endpoint = f"{function_url}/api/EmailReportService"
        api_key = os.environ.get("INTERNAL_API_KEY")

        headers = {
            "Content-Type": "application/json",
            "x-functions-key": api_key
        }
        
        # Call the HTTP endpoint (no parameters = yesterday's data for all circles)
        response = requests.post(
            endpoint,
            json={}, 
            headers=headers, 
            timeout=600  # 10 minutes timeout
        )
        
        if response.status_code == 200:
            result = response.json()
            logging.info(f'✅ Email sending successful: {result}')
        else:
            logging.error(f'❌ Email sending failed: {response.status_code} - {response.text}')
            
    except Exception as e:
        logging.error(f'❌ Error calling email endpoint: {str(e)}', exc_info=True)