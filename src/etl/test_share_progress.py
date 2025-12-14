"""
Test script to simulate sharing daily progress to Slack
"""
import requests
import json

# Test data - simplified, only userId needed now
test_data = {
    "userId": "cdadab2a-d6c3-4270-ac47-ac70831cb851",  # ← твой реальный userId
    "comment": "What I did today: finished the module on routing 🚀"  # Optional
}

def test_share_to_slack():
    """
    Test the ShareProgressToSlack function
    """
    print("\n🧪 Testing ShareProgressToSlack function (Daily Summary)...\n")
    
    # Local Azure Functions URL
    url = "http://localhost:7071/api/share-progress-to-slack"
    
    print(f"📤 Sending request to: {url}")
    print(f"📋 Request body: {json.dumps(test_data, indent=2)}\n")
    
    try:
        response = requests.post(
            url,
            json=test_data,
            headers={"Content-Type": "application/json"}
        )
        
        print(f"📊 Status Code: {response.status_code}")
        print(f"📄 Response: {json.dumps(response.json(), indent=2)}\n")
        
        if response.status_code == 200:
            result = response.json()
            print("✅ Success! Check your Slack channel for the message!")
            print(f"\n📈 Summary:")
            print(f"   Activities shared: {result.get('activitiesCount', 0)}")
            print(f"   Completed: {result.get('completedCount', 0)}")
            print(f"   Total time: {result.get('totalTimeSeconds', 0)} seconds")
        else:
            print("❌ Failed! Check the error above.")
            
    except requests.exceptions.ConnectionError:
        print("❌ Connection Error!")
        print("   Make sure Azure Functions is running:")
        print("   Run: func start")
    except Exception as e:
        print(f"❌ Error: {str(e)}")


if __name__ == "__main__":
    print("\n" + "="*60)
    print("SHARE DAILY PROGRESS TO SLACK - TEST")
    print("="*60)
    
    print("\n📝 This will share ALL progress from last 24 hours")
    print(f"   User ID: {test_data['userId']}")
    print(f"   Comment: {test_data.get('comment', 'No comment')}")
    
    input("\nPress Enter to continue with test...")
    
    test_share_to_slack()
    
    print("\n" + "="*60 + "\n")