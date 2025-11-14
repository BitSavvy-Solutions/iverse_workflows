import os
from pymongo import MongoClient
from datetime import datetime, timezone

connection_string = os.environ['COSMOS_CONNECTION_STRING']
client = MongoClient(connection_string)

course_db = client['coursedb']
progress_collection = course_db['progress']

# Insert test progress for user456 (from your circle)
test_progress = {
    "userId": "user456",
    "courseId": "course123",
    "materialId": "material_async",
    "commitSha": "abc123",
    "materialHash": "hash456",
    "timeSpentSeconds": 3600,
    "updateText": "Completed async/await exercises",
    "isCompleted": True,
    "submittedAt": datetime.now(timezone.utc)
}

result = progress_collection.insert_one(test_progress)
print(f"✅ Test progress inserted: {result.inserted_id}")
client.close()