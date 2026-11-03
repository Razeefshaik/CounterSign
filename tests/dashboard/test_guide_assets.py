"""C57: guide files are served locally; no database, model or PayPal calls."""

from types import SimpleNamespace

import httpx
import pytest

from countersign.config import Settings
from countersign.main import create_hosted_app


@pytest.mark.asyncio
async def test_hosted_app_serves_guided_start_assets(monkeypatch):
    monkeypatch.delenv("COUNTERSIGN_DASHBOARD_SOURCE", raising=False)
    fake = SimpleNamespace()
    app = create_hosted_app(
        settings=Settings("postgresql://fake:fake@localhost/countersign_c57"),
        database=fake, http=fake, reader=fake, verifier=fake,
    )
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client,
    ):
        page = await client.get("/")
        script = await client.get("/guide.mjs")
        style = await client.get("/guide.css")
    assert page.status_code == script.status_code == style.status_code == 200
    assert "text/javascript" in script.headers["content-type"]
    assert "text/css" in style.headers["content-type"]
    assert '<section id="week-guide"' in page.text
    assert page.text.index('id="week-guide"') > page.text.index('id="hero-motion"')
    assert page.text.index('id="week-guide"') < page.text.index('id="cc-action"')
    assert 'aria-label="Staged week guidance" hidden' in page.text
