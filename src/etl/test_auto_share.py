"""
Test script for AutoShareDailyProgress function
Simulates the timer trigger locally
"""
import sys
import os
from datetime import datetime, timezone

# Add parent directory to path to import the function
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from AutoShareDailyProgress import main


class MockTimerRequest:
    """Mock timer request for testing"""
    def __init__(self):
        self.past_due = False


def test_auto_share():
    """
    Test the AutoShareDailyProgress function locally
    """
    print("\n" + "="*60)
    print("AUTO SHARE DAILY PROGRESS - TEST")
    print("="*60)
    print(f"\nCurrent time (UTC): {datetime.now(timezone.utc)}")
    print("\nThis will find students who:")
    print("  ✓ Have progress entries today")
    print("  ✗ Haven't manually shared to Slack")
    print("\nAnd auto-share their progress!\n")
    
    input("Press Enter to start test...\n")
    
    print("🚀 Running AutoShareDailyProgress function...\n")
    
    try:
        # Create mock timer request
        mock_timer = MockTimerRequest()
        
        # Call the function
        main(mock_timer)
        
        print("\n" + "="*60)
        print("✅ TEST COMPLETED SUCCESSFULLY")
        print("="*60)
        print("\n📱 Check your Slack channel for auto-shared messages!")
        
    except Exception as e:
        print("\n" + "="*60)
        print("❌ TEST FAILED")
        print("="*60)
        print(f"\nError: {str(e)}")


if __name__ == "__main__":
    test_auto_share()
