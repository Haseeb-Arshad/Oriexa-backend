import httpx

def main():
    task_id = 1961
    url = f"http://127.0.0.1:8000/api/v1/tasks/{task_id}/claims"
    # To act as the task poster, use User ID 1 and the same API key used to create it
    # No, wait. We use the task poster's User ID via X-User-ID or we use the Freelancer key.
    # The accept endpoint expects the User who posted the task.
    headers = {
        "Authorization": "Bearer th_agent_a801b587552cda97f5aaece438827c39ccf6356980205f088acc38d58ec62ae8",
        "X-User-ID": "1",
        "Content-Type": "application/json"
    }
    
    url = f"http://127.0.0.1:8000/api/v1/tasks/{task_id}"
    
    with httpx.Client(timeout=10.0) as client:
        resp = client.get(url, headers=headers)
        print("Task Response:", resp.status_code)
        data = resp.json()
        
        main_task = data.get("data", {}) if isinstance(data, dict) else data
        claims = main_task.get("claims", [])
        for c in claims:
            if c.get("status") == "pending":
                print(f"Accepting claim {c['id']}")
                accept_resp = client.post(f"http://127.0.0.1:8000/api/v1/tasks/{task_id}/claims/accept", json={"claim_id": c['id']}, headers=headers)
                print("Accept Result:", accept_resp.status_code, accept_resp.text)

if __name__ == "__main__":
    main()
