"""C49: the hosted app serves the Community grid locally, without a CDN."""

from types import SimpleNamespace

import httpx
import pytest

from countersign.config import Settings
from countersign.main import create_hosted_app


@pytest.mark.asyncio
async def test_hosted_app_serves_vendored_grid_and_lazy_bridge(monkeypatch):
    monkeypatch.delenv("COUNTERSIGN_DASHBOARD_SOURCE", raising=False)
    fake = SimpleNamespace()
    app = create_hosted_app(
        settings=Settings("postgresql://fake:fake@localhost/countersign_c49"),
        database=fake, http=fake, reader=fake, verifier=fake,
    )
    files = {
        "/vendor/ag-grid-community.min.js": "javascript",
        "/vendor/ag-grid.css": "text/css",
        "/vendor/ag-theme-quartz.css": "text/css",
        "/vendor/LICENSE.txt": "text/plain",
        "/grid.mjs": "text/javascript",
        "/grid.css": "text/css",
    }
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client,
    ):
        responses = {path: await client.get(path) for path in files}
        page = await client.get("/")
    for path, response in responses.items():
        assert response.status_code == 200, path
        assert files[path] in response.headers["content-type"], path
        assert response.content, path
    assert "ag-grid-community.min.js" not in page.text
    assert "32.3.9" in responses["/vendor/ag-grid-community.min.js"].text
    assert "MIT" in responses["/vendor/LICENSE.txt"].text
