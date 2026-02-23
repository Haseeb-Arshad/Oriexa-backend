import httpx
import json

def create_vercel_task():
    url = "http://127.0.0.1:8000/api/v1/tasks"
    headers = {
        "X-User-ID": "1",
        "Content-Type": "application/json",
        "Authorization": "Bearer th_agent_a801b587552cda97f5aaece438827c39ccf6356980205f088acc38d58ec62ae8"
    }
    
    payload = {
        "title": "Modern Glassmorphic Digital Clock",
        "description": "Create a stunning, interactive Digital Clock using React/Next.js and Tailwind CSS. The design should feature a premium 'Glassmorphism' aesthetic (vibrant gradients, frosted glass effect, backdrop blur). Include a toggle for 12h/24h format. MUST be deployable to Vercel.",
        "requirements": (
            "- Use Next.js (App Router) and Tailwind CSS\n"
            "- Implement High-End Glassmorphism UI\n"
            "- Add format toggle (12h/24h)\n"
            "- Ensure it passes a basic smoke test\n"
            "- Deployment to Vercel is MANDATORY"
        ),
        "budget_credits": 5000,
        "auto_review_enabled": True
    }
    
    print(f"Creating Vercel Demo Task...")
    try:
        with httpx.Client(timeout=30.0) as client:
            response = client.post(url, json=payload, headers=headers)
            if response.status_code == 201:
                data = response.json()
                task_id = data.get("data", {}).get("id")
                print(f"✅ Success! Task ID: {task_id}")
                return task_id
            else:
                print(f"❌ Failed: {response.status_code}")
                print(response.text)
    except Exception as e:
        print(f"Error: {e}")
    return None

if __name__ == "__main__":
    create_vercel_task()
