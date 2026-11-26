"""Pages retired once Langfuse became the record of coaching calls (#180)."""

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("path", ["/prompt-logs", "/prompt-logs/1"])
async def test_the_logs_page_is_gone(client: AsyncClient, path: str) -> None:
    resp = await client.get(path)
    assert resp.status_code == 404


async def test_the_navigation_has_no_logs_link(client: AsyncClient) -> None:
    resp = await client.get("/")
    assert resp.status_code == 200
    assert "/prompt-logs" not in resp.text
    assert ">Logs<" not in resp.text
