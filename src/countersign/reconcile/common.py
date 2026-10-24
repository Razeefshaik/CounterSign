"""Safe reconciliation results and deduplicated investigation records."""

import re
from dataclasses import dataclass

from psycopg.types.json import Jsonb

from countersign.db import Repositories
from countersign.fsm.attempt import AttemptState
from countersign.fsm.dispute import Phase
from countersign.fsm.line import LineState
from countersign.fsm.obligation import ObligationState


class ReconcileError(RuntimeError):
    """Fixed public failure text; never wrap private transport or database values."""


@dataclass
class StageResult:
    seen: int = 0
    updated: int = 0
    skipped: int = 0
    incidents: int = 0
    errors: int = 0
    unknown: int = 0
    prepared: int = 0


_STATES = frozenset(
    str(value) for enum in (AttemptState, Phase, LineState, ObligationState) for value in enum
)


def _state(value):
    if value is None:
        return None
    return str(value) if isinstance(value, str) and value in _STATES else "UNRECOGNIZED"


async def open_incident(conn, entity: str, row_id, reason: str, *, before=None, after=None) -> bool:
    """Caller holds the owner-decision lock; repeat observations reuse an open incident.

    Only controlled identifiers and known state names enter public audit details.
    Verbatim external responses belong in private resource mirrors, not incidents.
    """
    identity = str(row_id)
    if not re.fullmatch(r"[a-z_]+", entity) or not re.fullmatch(r"[A-Za-z0-9_-]+", identity):
        raise ReconcileError("Invalid reconciliation subject")
    if not re.fullmatch(r"[A-Z0-9_]+", reason):
        raise ReconcileError("Invalid reconciliation reason")
    subject = {
        "entity": entity,
        "id": identity,
        "reason": reason,
        "before": _state(before),
        "after": _state(after),
    }
    cursor = await conn.execute(
        "SELECT id FROM incidents WHERE kind='RECONCILIATION' AND subject=%s "
        "AND state<>'RESOLVED' ORDER BY id LIMIT 1",
        (Jsonb(subject),),
    )
    if await cursor.fetchone():
        return False
    repos = Repositories(conn)
    incident = await repos.incidents.insert(kind="RECONCILIATION", subject=subject, state="OPEN")
    await repos.audit_log.insert(
        actor="system",
        kind="INCIDENT_OPENED",
        details={"incident_id": str(incident["id"]), **subject},
    )
    return True
