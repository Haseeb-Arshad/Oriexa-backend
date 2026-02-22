import httpx
import json

def register_agent():
    url = "http://127.0.0.1:8000/api/v1/user/agents"
    headers = {
        "X-User-ID": "1",
        "Content-Type": "application/json"
    }
    payload = {
        "name": "System Reviewer",
        "description": "Automated reviewer agent for verifying task deliverables.",
        "capabilities": ["review", "qa", "llm"]
    }
    
    print(f"Sending request to {url}...")
    try:
        response = httpx.post(url, json=payload, headers=headers, timeout=30.0)
        print(f"Status: {response.status_code}")
        print(f"Response: {json.dumps(response.json(), indent=2)}")
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    register_agent()
