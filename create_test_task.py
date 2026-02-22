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
        "title": "Automated Swarm Test: Prime Check",
        "description": "Implement a Python function `is_prime(n)` that returns True if n is prime, False otherwise. Optimize for performance.",
        "requirements": "- Python 3.10+\n- Efficient implementation\n- Proper type hints",
        "budget_credits": 100,
        "auto_review_enabled": True
    }
    
    print(f"Creating task...")
    try:
        with httpx.Client(timeout=30.0) as client:
            # We need an agent API key to post a task if using the API
            # Let's use the Freelancer key for now as a 'poster' bot
            headers["Authorization"] = "Bearer th_agent_6d25839eedb71d5d2bd5a0956e04fd803d1c7e540bcect0abe11b0d7cbfd64ca8"
            response = client.post(url, json=payload, headers=headers)
            print(f"Status: {response.status_code}")
            print(f"Response: {json.dumps(response.json(), indent=2)}")
            return response.json().get("data", {}).get("id")
    except Exception as e:
        print(f"Error: {e}")
    return None

if __name__ == "__main__":
    create_task()
