import asyncio
from httpx import AsyncClient

async def test_search():
    async with AsyncClient(base_url="http://localhost:8000") as client:
        print("Testing Search Endpoint...")
        # Since we don't know a valid API key off hand, we'll try without one
        # and expect a 401 or 403, which still validates the routing works
        response = await client.get("/api/v1/tasks/search?q=test")
        print(f"Status Code: {response.status_code}")
        print(f"Response: {response.text}")

        # Also test with a fake key to see if it hits the auth layer correctly
        headers = {"Authorization": "Bearer th_agent_fakekey123"}
        response = await client.get("/api/v1/tasks/search?q=test", headers=headers)
        print(f"Status Code (with token): {response.status_code}")
        print(f"Response (with token): {response.text}")

if __name__ == "__main__":
    asyncio.run(test_search())
