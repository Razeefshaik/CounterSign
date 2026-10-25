"""The real attempt FSM, GET mirror and partial unique index agree on random histories."""

import psycopg
import pytest
from hardening_world import attempt, seed, state, update
from hypothesis import HealthCheck, example, given, settings
from hypothesis import strategies as st

from countersign.fsm.attempt import ATTEMPT, LIVE, PAYPAL_STATUSES, AttemptState, mirror

pytestmark = pytest.mark.integration
STATUSES = tuple(sorted(str(s) for s in PAYPAL_STATUSES))
COMMAND = st.tuples(
    st.sampled_from(("prepare", "submit", "ambiguous", "replay", "observe")),
    st.sampled_from(STATUSES),
)


@settings(
    max_examples=60, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture]
)
@given(commands=st.lists(COMMAND, min_size=1, max_size=45))
@example(
    commands=[
        ("prepare", "PENDING"),
        ("submit", "PENDING"),
        ("observe", "SUCCESS"),
        ("prepare", "PENDING"),
        ("ambiguous", "PENDING"),
        ("replay", "PENDING"),
        ("observe", "RETURNED"),
        ("prepare", "PENDING"),
    ]
)
def test_one_live_attempt_per_line_under_random_prepare_submit_resolve_sequences(
    db_schema, commands
):
    with db_schema.connect() as conn, conn.transaction(force_rollback=True):
        ids = seed(conn)
        latest = ids["attempt"]
        number = 1
        for command, paypal_status in commands:
            current = AttemptState(state(conn, "attempt", latest))
            if command == "prepare":
                if current in LIVE:
                    with pytest.raises(psycopg.errors.UniqueViolation) as error, conn.transaction():
                        attempt(conn, ids["line"], ids["operation"], number + 1, "PREPARED")
                    assert error.value.diag.constraint_name == "uq_one_live_attempt"
                else:
                    number += 1
                    latest = attempt(conn, ids["line"], ids["operation"], number, "PREPARED")
            elif command == "submit" and current is AttemptState.PREPARED:
                update(conn, "attempt", latest, ATTEMPT.next(current, "HTTP_2XX"))
            elif command == "ambiguous" and current is AttemptState.PREPARED:
                update(conn, "attempt", latest, ATTEMPT.next(current, "AMBIGUOUS"))
            elif command == "replay" and current is AttemptState.UNKNOWN:
                update(conn, "attempt", latest, ATTEMPT.next(current, "REPLAY_CONFIRMED"))
            elif command == "observe" and (
                current is AttemptState.SUBMITTED or current in PAYPAL_STATUSES
            ):
                target, _ = mirror(current, paypal_status)
                update(conn, "attempt", latest, target)
            rows = conn.execute(
                "SELECT state FROM payout_attempts WHERE line_id=%s", (ids["line"],)
            ).fetchall()
            live_count = sum(AttemptState(row["state"]) in LIVE for row in rows)
            assert live_count <= 1
            assert len(rows) == number


@settings(
    max_examples=100, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture]
)
@given(source=st.sampled_from(("SUBMITTED", *STATUSES)), target=st.sampled_from(STATUSES))
@example(source="SUCCESS", target="PENDING")
@example(source="SUBMITTED", target="REFUNDED")
def test_even_unexpected_get_mirrors_are_retained(db_schema, source, target):
    mirrored, expected = mirror(source, target)
    with db_schema.connect() as conn, conn.transaction(force_rollback=True):
        ids = seed(conn, {"attempt": source})
        update(conn, "attempt", ids["attempt"], mirrored)
        assert state(conn, "attempt", ids["attempt"]) == target
        if source == "SUCCESS" and target == "PENDING":
            assert not expected  # Caller opens an incident; the mirror itself must survive.
