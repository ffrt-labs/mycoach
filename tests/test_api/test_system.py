from httpx import AsyncClient


async def test_health_check(client: AsyncClient) -> None:
    response = await client.get("/api/system/status")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
