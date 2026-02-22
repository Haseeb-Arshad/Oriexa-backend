import httpx
import json

def register():
    url = "http://127.0.0.1:8000/api/v1/user/agents"
    headers = {"X-User-ID": "1", "Content-Type": "application/json"}
    payload = {
        "name": "LiveFreelancer",
        "description": "Final autonomous worker for live swarm.",
        "capabilities": ["python", "javascript", "sql"]
    }
    with httpx.Client(timeout=30.0) as client:
        resp = client.post(url, json=payload, headers=headers)
        data = resp.json()
        print(f"KEY: {data['api_key']}")

if __name__ == "__main__":
    register()
