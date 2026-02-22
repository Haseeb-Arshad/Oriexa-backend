import httpx
import json
import time

def clarify_task(task_id):
    url = f"http://127.0.0.1:8000/api/v1/user/tasks/{task_id}"
    headers = {
        "X-User-ID": "1",
        "Content-Type": "application/json"
    }
    
    # Simulate a user refining the task based on feedback
    payload = {
        "description": "I need a Python-based POS system for my retail shop. Features: Inventory tracking, barcode scanning, and daily sales reports. Must run on a local machine.",
        "requirements": "- Python 3.10+\n- SQLite database\n- Barcode scanner integration\n- PDF report generation"
    }
    
    print(f"Updating/Clarifying task {task_id}...")
    try:
        with httpx.Client(timeout=30.0) as client:
            response = client.patch(url, json=payload, headers=headers)
            print(f"Status: {response.status_code}")
            print(f"Response: {json.dumps(response.json(), indent=2)}")
            return response.json().get("success")
    except Exception as e:
        print(f"Error: {e}")
    return False

if __name__ == "__main__":
    import sys
    task_id = int(sys.argv[1]) if len(sys.argv) > 1 else 1882
    success = clarify_task(task_id)
    if success:
        print(f"\nTASK {task_id} CLARIFIED SUCCESSFULLY. Waiting for agent re-evaluation...")
