"""Demo metadata follows mock time; unavailable clocks fail closed."""

from datetime import UTC, datetime
from types import SimpleNamespace

import httpx
import pytest

from countersign.api import dashboard


def runtime(mode="demo"):
    return SimpleNamespace(
        mode=mode,
        paypal=SimpleNamespace(is_mock=mode == "demo", base_url="http://127.0.0.1:8099"),
    )


@pytest.mark.asyncio
async def test_demo_metadata_reads_mock_clock_and_tracks_advancement(monkeypatch):
    now = "2026-11-06T09:00:00Z"
    original = httpx.AsyncClient

    def respond(request):
        assert str(request.url) == "http://127.0.0.1:8099/_mock/clock"
        return httpx.Response(200, json={"now": now})

    monkeypatch.setattr(
        dashboard.httpx, "AsyncClient",
        lambda **kwargs: original(transport=httpx.MockTransport(respond), **kwargs),
    )
    source = dashboard.DbSource("postgresql://localhost/unit_test", runtime=runtime())
    assert (await source.singleton("meta", {}))["clock"] == now
    now = "2026-11-09T10:00:00Z"
    assert (await source.singleton("meta", {}))["clock"] == now


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [{}, {"now": "invalid"}, {"now": "2026-11-06T09:00:00"}])
async def test_invalid_demo_clock_never_falls_back_to_wall_time(monkeypatch, body):
    original = httpx.AsyncClient
    monkeypatch.setattr(
        dashboard.httpx, "AsyncClient",
        lambda **kwargs: original(
            transport=httpx.MockTransport(lambda request: httpx.Response(200, json=body)),
            **kwargs,
        ),
    )
    source = dashboard.DbSource("postgresql://localhost/unit_test", runtime=runtime())
    with pytest.raises(dashboard.DashboardUnavailable, match="Demo clock unavailable"):
        await source.singleton("meta", {})


@pytest.mark.asyncio
async def test_live_clock_does_not_contact_mock_and_explicit_engine_clock_is_supported(monkeypatch):
    def unexpected(**kwargs):
        pytest.fail("Live metadata must not query a mock")

    monkeypatch.setattr(dashboard.httpx, "AsyncClient", unexpected)
    source = dashboard.DbSource("postgresql://localhost/unit_test", runtime=runtime("live"))
    before = datetime.now(UTC)
    now = datetime.fromisoformat((await source.singleton("meta", {}))["clock"])
    assert before <= now <= datetime.now(UTC)
    staged = datetime(2026, 11, 6, 9, tzinfo=UTC)
    source = dashboard.DbSource(
        "postgresql://localhost/unit_test", runtime=runtime(), clock=lambda: staged,
    )
    assert (await source.singleton("meta", {}))["clock"] == "2026-11-06T09:00:00Z"
