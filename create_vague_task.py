import httpx
import json

def create_vague_task():
    url = "http://127.0.0.1:8000/api/v1/tasks"
    headers = {
        "X-User-ID": "1",
        "Content-Type": "application/json",
        "Authorization": "Bearer th_agent_54ed710118f848a783cdbc85b31fb0343882fec5651eae646e58fcf2f62e5d20"
    }
    payload = {
        "title": "Shop Software",
        "description": "I need software for my retail shop. It should handle everything I need for daily operations.",
        "requirements": "Must be easy to use.",
        "budget_credits": 50,
        "auto_review_enabled": True
    }
    
    print(f"Creating vague task...")
    try:
        with httpx.Client(timeout=30.0) as client:
            response = client.post(url, json=payload, headers=headers)
            print(f"Status: {response.status_code}")
            data = response.json()
            print(f"Response: {json.dumps(data, indent=2)}")
            return data.get("data", {}).get("id")
    except Exception as e:
        print(f"Error: {e}")
    return None

if __name__ == "__main__":
    task_id = create_vague_task()
    if task_id:
        print(f"\nCREATED VAGUE TASK: {task_id}")
