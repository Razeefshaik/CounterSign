"""The optional commander preserves recovery ordering and existing tick counts."""

from types import SimpleNamespace

import pytest

from countersign import worker


@pytest.mark.asyncio
@pytest.mark.parametrize("enabled", [False, True])
async def test_commander_runs_after_reconciliation_before_execution_and_sweep(monkeypatch, enabled):
    calls = []

    async def inbox(*args):
        calls.append("inbox")
        return {"processed_webhooks": 1}

    async def reconcile():
        calls.append("reconcile")
        return SimpleNamespace(**{name: SimpleNamespace(errors=0)
                                  for name in ("attempts", "disputes", "balances", "backfill")})

    async def investigate():
        calls.append("commander")

    async def execute(*args, **kwargs):
        calls.append("execute")
        assert kwargs["limit"] == 7
        return {"proposals": 1, "operations": 1, "errors": 0}

    async def sweep():
        calls.append("sweep")

    monkeypatch.setattr(worker, "_process_inbox", inbox)
    monkeypatch.setattr(worker, "execute_pending", execute)
    result = await worker.worker_once(
        object(), object(), SimpleNamespace(poll_once=reconcile), SimpleNamespace(sweep=sweep),
        commander=investigate if enabled else None, limit=7,
    )
    assert calls == ["inbox", "reconcile", *(["commander"] if enabled else []), "execute", "sweep"]
    assert result == {"processed_webhooks": 1, "proposals": 1, "operations": 1, "errors": 0}


@pytest.mark.asyncio
async def test_model_failure_is_counted_and_does_not_prevent_execution_recovery(monkeypatch):
    calls = []

    async def inbox(*args):
        return {"processed_webhooks": 0}

    async def reconcile():
        return SimpleNamespace(**{name: SimpleNamespace(errors=0)
                                  for name in ("attempts", "disputes", "balances", "backfill")})

    async def investigate():
        calls.append("commander")
        raise RuntimeError("FAKE-PRIVATE-MODEL-KEY")

    async def execute(*args, **kwargs):
        calls.append("execute")
        return {"proposals": 0, "operations": 0, "errors": 0}

    async def sweep():
        calls.append("sweep")

    monkeypatch.setattr(worker, "_process_inbox", inbox)
    monkeypatch.setattr(worker, "execute_pending", execute)
    result = await worker.worker_once(
        object(), object(), SimpleNamespace(poll_once=reconcile), SimpleNamespace(sweep=sweep),
        commander=investigate,
    )
    assert calls == ["commander", "execute", "sweep"]
    assert result == {"processed_webhooks": 0, "proposals": 0, "operations": 0, "errors": 1}
    assert "FAKE-PRIVATE-MODEL-KEY" not in repr(result)
