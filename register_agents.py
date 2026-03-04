import httpx
import json
import os

def register_agent(name, desc, caps):
    url = "http://127.0.0.1:8000/api/v1/user/agents"
    headers = {
        "X-User-ID": "1",
        "Content-Type": "application/json"
    }
    payload = {
        "name": name,
        "description": desc,
        "capabilities": caps
    }
    
    print(f"Registering {name}...")
    try:
        # Use verify=False to avoid SSL issues if any, although it's http
        with httpx.Client(timeout=30.0) as client:
            response = client.post(url, json=payload, headers=headers)
            if response.status_code == 201:
                data = response.json()
                key = data["data"]["api_key"]
                print(f"SUCCESS: {name} key = {key}")
                return key
            else:
                print(f"FAILED: {response.status_code} - {response.text}")
    except Exception as e:
        print(f"ERROR: {e}")
    return None

if __name__ == "__main__":
    agents = {}
    
    reviewer_key = register_agent(
        "System Reviewer", 
        "Automated reviewer agent for verifying task deliverables.", 
        ["review", "qa", "llm"]
    )
    if reviewer_key:
        agents["reviewer"] = reviewer_key
        
    freelancer_key = register_agent(
        "AutoWorker Bot", 
        "Autonomous AI worker agent that claims and delivers tasks.", 
        ["python", "javascript", "sql"]
    )
    if freelancer_key:
        agents["freelancer"] = freelancer_key
        
    with open("agent_keys.json", "w") as f:
        json.dump(agents, f)
    print("\nSaved keys to agent_keys.json")
