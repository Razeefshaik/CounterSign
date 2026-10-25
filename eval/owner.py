"""Owner policy scheduling runs on mock time, never sleeps or wall-clock timers."""

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timedelta

from eval.systems.base import OwnerRequest, System

OWNER_ACTIONS = {
    "SCHEDULE_PAYOUT_RUN",
    "CREATE_REPLACEMENT_PAYOUT",
    "DEFER_SCHEDULED_PAYOUT",
    "ACCEPT_CLAIM",
}


def expected_request(request: OwnerRequest, expected: dict) -> bool:
    return any(
        request.action == entry["action"] and request.total == entry.get("total")
        for entry in expected["owner_should_approve"]
    )


@dataclass
class OwnerDecision:
    request: OwnerRequest
    approved: bool
    at: str
    payout_ids_before: tuple[str, ...] = ()
    refunds_before: int = 0


class OwnerPolicies:
    def __init__(self, expected: dict):
        self.expected = deepcopy(expected)
        self.policy: str | None = None
        self.delay = 0
        self.seen: dict[str, OwnerRequest] = {}
        self.due: dict[str, datetime] = {}
        self.decisions: list[OwnerDecision] = []
        self.decided: set[str] = set()
        self.observe_truth = None

    def configure(self, policy: str, seconds: int = 0) -> None:
        if policy not in {"approve_expected", "approve_all", "reject_all", "approve_after"}:
            raise ValueError("Unknown owner policy")
        if type(seconds) is not int or seconds < 0:
            raise ValueError("Invalid owner delay")
        self.policy, self.delay = policy, seconds
        self.due.clear()

    def next_due(self, now: datetime) -> datetime | None:
        future = [
            value for key, value in self.due.items() if key not in self.decided and value > now
        ]
        return min(future, default=None)

    async def apply(self, system: System, now: datetime) -> None:
        requests = await system.pending_owner_requests()
        for request in requests:
            if (
                not isinstance(request.id, str)
                or not request.id
                or not isinstance(request.params, dict)
            ):
                raise ValueError("Invalid owner request")
            if request.total is not None and (
                not isinstance(request.total, dict)
                or type(request.total.get("minor")) is not int
                or request.total.get("currency") != "USD"
            ):
                raise ValueError("Invalid owner request total")
        pending = {r.id: r for r in requests}
        if len(pending) != len(requests):
            raise ValueError("Duplicate owner request IDs")
        # A withdrawn request cannot keep a delayed approval scheduled forever.
        self.due = {key: value for key, value in self.due.items() if key in pending}
        for key, request in pending.items():
            if key in self.seen and self.seen[key] != request:
                raise ValueError("Owner request changed under the same ID")
            self.seen.setdefault(key, deepcopy(request))
            if self.policy is None or key in self.decided:
                continue
            if self.policy == "approve_after":
                due = self.due.setdefault(key, now + timedelta(seconds=self.delay))
                if now < due:
                    continue
            approved = self.policy in {"approve_all", "approve_after"} or (
                self.policy == "approve_expected" and expected_request(request, self.expected)
            )
            # Record an approval only if the system accepted the decision call.
            before = await self.observe_truth() if approved and self.observe_truth else {}
            await system.owner_decide(key, approved)
            self.decided.add(key)
            self.due.pop(key, None)
            self.decisions.append(
                OwnerDecision(
                    deepcopy(request),
                    approved,
                    now.isoformat().replace("+00:00", "Z"),
                    tuple(p["payout_item_id"] for p in before.get("payout_items", [])),
                    len(before.get("refunds", [])),
                )
            )

    @property
    def unnecessary(self) -> int:
        return sum(
            request.action not in OWNER_ACTIONS and not expected_request(request, self.expected)
            for request in self.seen.values()
        )
