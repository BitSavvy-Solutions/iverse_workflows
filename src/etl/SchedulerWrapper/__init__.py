import logging
import azure.functions as func
import requests
import os

def main(myTimer: func.TimerRequest) -> None:
    """
    Timer trigger that calls the HTTP endpoint daily.
    This is just a thin wrapper - all logic is in the HTTP function.
    """
    logging.info('Timer triggered - calling DailyReportGenerator endpoint')
    
    try:
        # Get function URL (local or deployed)
        function_url = os.environ.get('FUNCTION_APP_URL', 'http://localhost:7071')
        endpoint = f"{function_url}/api/DailyReportGenerator"
        
        # Call the HTTP endpoint (no parameters = yesterday's data for all circles)
        response = requests.post(
            endpoint,
            json={},  
            timeout=600  # 10 minutes timeout
        )
        
        if response.status_code == 200:
            result = response.json()
            logging.info(f'✅ Report generation successful: {result}')
        else:
            logging.error(f'❌ Report generation failed: {response.status_code} - {response.text}')
            
    except Exception as e:
        logging.error(f'❌ Error calling report endpoint: {str(e)}', exc_info=True)