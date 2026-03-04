import httpx
import json

def create_task():
    url = "http://127.0.0.1:8000/api/v1/tasks"
    # Use the freelancer agent's operator (User 1) to post the task
    headers = {
        "X-User-ID": "1",
        "Content-Type": "application/json"
    }
    payload = {
        "title": "High-Performance Matrix Optimizer",
        "description": "Implement a state-of-the-art Pure Python algorithm for matrix heuristic optimizations. MUST be strictly one single python file `main.py`. You MUST use TDD by including a basic `test_matrix()` function within `main.py`.",
        "requirements": "- Pure Python 3.10+\n- Advanced mathematical matrix manipulations\n- STRICTLY 1 FILE TOTAL\n- KEEP CODE UNDER 80 LINES",
        "budget_credits": 2500,
        "auto_review_enabled": True
    }
    
    print(f"Creating task...")
    try:
        with httpx.Client(timeout=30.0) as client:
            # We need an agent API key to post a task if using the API
            # Let's use the Freelancer key for now as a 'poster' bot
            headers["Authorization"] = "Bearer th_agent_a801b587552cda97f5aaece438827c39ccf6356980205f088acc38d58ec62ae8"
            response = client.post(url, json=payload, headers=headers)
            print(f"Status: {response.status_code}")
            print(f"Response: {json.dumps(response.json(), indent=2)}")
            return response.json().get("data", {}).get("id")
    except Exception as e:
        print(f"Error: {e}")
    return None

if __name__ == "__main__":
    create_task()
