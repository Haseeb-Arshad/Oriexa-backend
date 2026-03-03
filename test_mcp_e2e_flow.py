import asyncio
import os
import httpx
from sqlalchemy import select
from app.db.engine import async_session
from app.db.models import User, Agent
from app.auth.password import hash_password

async def setup_db_agents():
    # Helper to create poster & freelancer directly in DB
    async with async_session() as session:
        # Poster User
        poster = User(email=f"p_{os.urandom(4).hex()}@test.com", name="MCP Poster", role="both")
        session.add(poster)
        await session.flush()
        
        poster_agent = Agent(operator_id=poster.id, name="Test Poster", description="Test poster description", capabilities=["posting"])
        session.add(poster_agent)
        
        # Free User
        free = User(email=f"f_{os.urandom(4).hex()}@test.com", name="MCP Freelancer", role="both")
        session.add(free)
        await session.flush()
        
        free_agent = Agent(operator_id=free.id, name="Test Free", description="Test free description", capabilities=["coding"])
        session.add(free_agent)
        
        await session.commit()
        await session.refresh(poster_agent)
        await session.refresh(free_agent)
        
        return poster_agent.api_key, free_agent.api_key

async def run_e2e_test():
    print("--- Testing Oriexa E2E via API directly ---")
    
    poster_key, free_key = await setup_db_agents()
    print(f"Poster Key: {poster_key[:10]}...")
    print(f"Free Key: {free_key[:10]}...")

    async with httpx.AsyncClient(base_url="http://localhost:8000") as http:
        print("\n1. Poster creates task...")
        task_resp = await http.post(
            "/api/v1/tasks",
            headers={"Authorization": f"Bearer {poster_key}"},
            json={
                "title": f"Build a test app {os.urandom(2).hex()}",
                "description": "Please verify everything works. Use React and CSS.",
                "budget_credits": 100,
                "deadline_days": 7
            }
        )
        task_id = task_resp.json()["data"]["id"]
        print(f"Created Task ID: {task_id}")

        print("\n2. Freelancer searches for task via Search Endpoint...")
        search_resp = await http.get(
            "/api/v1/tasks/search?q=React",
            headers={"Authorization": f"Bearer {free_key}"}
        )
        print(f"Search Results matched: {len(search_resp.json()['data'])} tasks")

        print(f"\n3. Freelancer claims Task {task_id}...")
        claim_resp = await http.post(
            f"/api/v1/tasks/{task_id}/claims",
            headers={"Authorization": f"Bearer {free_key}"},
            json={
                "proposed_credits": 100,
                "message": "I can build this in React!"
            }
        )
        claim_id = claim_resp.json()["data"]["id"]
        print(f"Claim ID: {claim_id}")

        print("\n4. Poster lists task claims via new Claims List Endpoint...")
        claims_list = await http.get(
            f"/api/v1/tasks/{task_id}/claims",
            headers={"Authorization": f"Bearer {poster_key}"}
        )
        print(f"Poster sees claims: {len(claims_list.json()['data'])}")

        print("\n5. Poster accepts claim...")
        accept_resp = await http.post(
            f"/api/v1/tasks/{task_id}/claims/accept",
            headers={"Authorization": f"Bearer {poster_key}"},
            json={"claim_id": claim_id}
        )
        print(f"Accept status: {accept_resp.status_code}")

        print("\n6. Freelancer starts task...")
        start_resp = await http.post(
            f"/api/v1/tasks/{task_id}/start",
            headers={"Authorization": f"Bearer {free_key}"}
        )
        print(f"Start Action: {start_resp.status_code}")

        print("\n7. Freelancer submits deliverable...")
        deliver_resp = await http.post(
            f"/api/v1/tasks/{task_id}/deliverables",
            headers={"Authorization": f"Bearer {free_key}"},
            json={
                "content": "Here is the code. Visit https://example.com for proof.",
                "message": "Done!"
            }
        )
        del_data = deliver_resp.json().get("data", {})
        del_id = del_data.get("id")
        print(f"Deliverable ID: {del_id}")
        if not del_id:
            print(f"Deliver Failed: {deliver_resp.text}")
            return

        print("\n8. Reviewer Agent (poster key) submits review via new Review Endpoint...")
        review_resp = await http.post(
            f"/api/v1/tasks/{task_id}/review",
            headers={"Authorization": f"Bearer {poster_key}"},
            json={
                "deliverable_id": del_id,
                "verdict": "pass",
                "feedback": "Perfect test!",
                "key_source": "poster"
            }
        )
        print(f"Review response: {review_resp.json().get('data', review_resp.text)}")

        print("\n9. Checking Final Task Status...")
        final_task = await http.get(
            f"/api/v1/tasks/{task_id}",
            headers={"Authorization": f"Bearer {poster_key}"}
        )
        print(f"Final Task Status: {final_task.json()['data']['status']}")

if __name__ == "__main__":
    asyncio.run(run_e2e_test())
