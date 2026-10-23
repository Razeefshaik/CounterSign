"""M07 read-only dashboard API. Fixtures now; DbSource is a later card."""

import base64
import hashlib
import json
import re
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Protocol

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

FIXTURES = Path(__file__).resolve().parents[3] / "web" / "fixtures"
LISTS = {
    "incidents": ({"state", "kind"}, "updated_at", True),
    "proposals": ({"incident_id", "state", "verdict", "action"}, "created_at", True),
    "payout-runs": ({"state"}, "week_start", True),
    "makers": (set(), "display_name", False),
    "disputes": ({"phase", "status"}, "updated_at", True),
    "reservations": ({"state", "kind", "dispute_id", "run_id"}, "created_at", True),
    "audit-log": (
        {"proposal_id", "operation_id", "actor", "kind", "since"},
        "id",
        True,
    ),
}


class DashboardSource(Protocol):
    async def list(
        self, resource: str, filters: dict, limit: int, cursor: str | None
    ) -> tuple[list[dict], str | None]: ...
    async def get(self, resource: str, id: str) -> dict | None: ...
    async def singleton(self, resource: str, params: dict) -> dict: ...


def masked(value):
    if isinstance(value, dict):
        return {key: masked(item) for key, item in value.items()}
    if isinstance(value, list):
        return [masked(item) for item in value]
    if isinstance(value, str):
        return re.sub(
            r"(?<![\w*])([A-Za-z0-9._%+-]+)@([A-Za-z0-9.-]+\.[A-Za-z]{2,})",
            lambda m: m[1][0] + "***@" + m[2],
            value,
        )
    return value


class FixtureSource:
    """Contract fixtures. Cursors bind resource, filters, and fixture revision."""

    def __init__(self, directory: Path = FIXTURES):
        self.docs = {
            path.stem: json.loads(path.read_text(encoding="utf-8"))
            for path in directory.glob("*.json")
        }
        self.docs = masked(self.docs)
        # Contract's run detail example abbreviates its second new-payment line.
        for run in self.docs["run-details"]:
            if len(run["lines"]) < run["line_count"]:
                line = deepcopy(run["lines"][0])
                line.update(
                    id="0192f1c4-7a10-7c3e-9b1a-5d2f00000702",
                    maker={
                        "id": "0192f1c4-7a10-7c3e-9b1a-5d2f00000303",
                        "display_name": "Maker C",
                    },
                    amount={"minor": 30000, "currency": "USD"},
                    obligations=[],
                    attempts=[],
                )
                run["lines"].append(line)
        for maker in self.docs["makers"]["items"]:
            name = maker["display_name"]
            maker["reserved"]["minor"] = {
                "Maker A": 36000,
                "Maker B": 5400,
                "Maker C": 0,
            }[name]
            maker["scheduled"]["minor"] = {
                "Maker A": 0,
                "Maker B": 48600,
                "Maker C": 60000,
            }[name]
        for proposal in self.docs["proposals"]["items"]:
            if proposal["action"] == "SCHEDULE_PAYOUT_RUN":
                run = self.docs["run-details"][0]
                proposal["params"]["lines"] = [
                    {
                        "line_id": line["id"],
                        "maker_id": line["maker"]["id"],
                        "amount": deepcopy(line["amount"]),
                    }
                    for line in run["lines"]
                ]
                proposal["approval_request"]["payload_hash"] = (
                    "sha256:"
                    + hashlib.sha256(
                        json.dumps(
                            proposal["params"], sort_keys=True, separators=(",", ":")
                        ).encode()
                    ).hexdigest()
                )
        reservation = deepcopy(self.docs["reservations"]["items"][0])
        reservation.update(
            id="0192f1c4-7a10-7c3e-9b1a-5d2f00000b02",
            amount={"minor": 5400, "currency": "USD"},
            obligation_id="0192f1c4-7a10-7c3e-9b1a-5d2f00000803",
            dispute_id="0192f1c4-7a10-7c3e-9b1a-5d2f00000102",
            reason="Maker B share of disputed order #1052",
        )
        self.docs["reservations"]["items"].append(reservation)
        graph = self.docs["money-graph"]

        def node(id, type, label, state, amount):
            if not any(n["id"] == id for n in graph["nodes"]):
                graph["nodes"].append(
                    {
                        "id": id,
                        "type": type,
                        "label": label,
                        "state": state,
                        "amount": amount,
                    }
                )

        def edge(source, target, type):
            graph["edges"].append({"from": source, "to": target, "type": type})

        for run in self.docs["run-details"]:
            for line in run["lines"] + run["replacements"]:
                id = line.get("id") or line["line_id"]
                lid, mid = "line:" + id, "maker:" + line["maker"]["id"]
                node(mid, "maker", line["maker"]["display_name"], None, None)
                node(
                    lid,
                    "line",
                    line["maker"]["display_name"] + " payout",
                    line.get("state", "UNPAID"),
                    line["amount"],
                )
                edge(lid, mid, "pays")
                for obligation in line.get("obligations", []):
                    oid = "obligation:" + obligation["id"]
                    node(
                        oid,
                        "obligation",
                        obligation["order_ref"],
                        obligation["state"],
                        obligation["amount"],
                    )
                    edge(oid, lid, "allocated_to")
                for attempt in line.get("attempts", []):
                    aid = "attempt:" + attempt["id"]
                    node(
                        aid,
                        "attempt",
                        "Attempt " + str(attempt["attempt_no"]),
                        attempt["state"],
                        attempt["amount"],
                    )
                    edge(lid, aid, "attempted_by")
        self.revision = hashlib.sha256(
            json.dumps(self.docs, sort_keys=True).encode()
        ).hexdigest()[:16]

    async def list(self, resource, filters, limit, cursor):
        allowed, sort, descending = LISTS[resource]
        if filters.keys() - allowed or not 1 <= limit <= 200:
            raise ValueError("Invalid filter or limit")
        if "since" in filters:
            since = datetime.fromisoformat(filters["since"])
            if since.tzinfo is None:
                raise ValueError("Timezone required")
        rows = deepcopy(self.docs[resource]["items"])
        rows = [
            row
            for row in rows
            if all(
                datetime.fromisoformat(row["at"]) >= datetime.fromisoformat(value)
                if key == "since"
                else row.get(key) == value
                for key, value in filters.items()
            )
        ]
        rows.sort(key=lambda row: (row[sort], str(row["id"])), reverse=descending)
        offset = 0
        if cursor:
            try:
                data = json.loads(base64.urlsafe_b64decode(cursor).decode())
                if (
                    data["resource"] != resource
                    or data["filters"] != filters
                    or data["revision"] != self.revision
                ):
                    raise ValueError("Cursor does not match query")
                offset = data["offset"]
                if type(offset) is not int or offset < 0:
                    raise ValueError("Invalid offset")
            except (ValueError, TypeError, KeyError, UnicodeError):
                raise ValueError("Invalid cursor") from None
        page = rows[offset : offset + limit]
        next_cursor = None
        if offset + limit < len(rows):
            next_cursor = base64.urlsafe_b64encode(
                json.dumps(
                    {
                        "resource": resource,
                        "filters": filters,
                        "revision": self.revision,
                        "offset": offset + limit,
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode()
            ).decode()
        return page, next_cursor

    async def get(self, resource, id):
        if resource == "payout-runs":
            return deepcopy(
                next((r for r in self.docs["run-details"] if r["id"] == id), None)
            )
        doc = deepcopy(
            next((r for r in self.docs[resource]["items"] if r["id"] == id), None)
        )
        if doc is not None and resource == "incidents":
            doc["proposals"] = sorted(
                [
                    deepcopy(p)
                    for p in self.docs["proposals"]["items"]
                    if p["incident_id"] == id
                ],
                key=lambda p: p["created_at"],
                reverse=True,
            )
            ids = {p["id"] for p in doc["proposals"]}
            doc["timeline"] = sorted(
                [
                    deepcopy(a)
                    for a in self.docs["audit-log"]["items"]
                    if a["proposal_id"] in ids
                ],
                key=lambda a: a["at"],
            )
        return doc

    async def singleton(self, resource, params):
        if resource != "money-graph":
            if params:
                raise ValueError("Unexpected query parameters")
            return deepcopy(self.docs[resource])
        if len(params) != 1 or not set(params) <= {"dispute_id", "maker_id", "run_id"}:
            raise ValueError("Choose one graph root")
        kind, id = next(iter(params.items()))
        kind = {"dispute_id": "dispute", "maker_id": "maker", "run_id": "run"}[kind]
        doc = deepcopy(self.docs["money-graph"])
        root = kind + ":" + id
        if kind == "run":
            run = next((r for r in self.docs["run-details"] if r["id"] == id), None)
            reachable = (
                {
                    "line:" + (line.get("id") or line["line_id"])
                    for line in run["lines"] + run["replacements"]
                }
                if run
                else {root}
            )
        else:
            reachable = {root}
        while True:
            added = {e["to"] for e in doc["edges"] if e["from"] in reachable} | {
                e["from"] for e in doc["edges"] if e["to"] in reachable
            }
            if added <= reachable:
                break
            reachable |= added
        doc["nodes"] = [node for node in doc["nodes"] if node["id"] in reachable]
        doc["edges"] = [
            edge
            for edge in doc["edges"]
            if edge["from"] in reachable and edge["to"] in reachable
        ]
        return doc


def error(status, code, message):
    return JSONResponse(
        status_code=status, content={"error": {"code": code, "message": message}}
    )


def build_router(source: DashboardSource | None = None):
    source = source or FixtureSource()
    result = APIRouter()

    def list_endpoint(resource):
        async def endpoint(request: Request):
            params = dict(request.query_params)
            try:
                limit = int(params.pop("limit", "50"))
                cursor = params.pop("cursor", None)
                if not 1 <= limit <= 200:
                    return error(
                        422, "INVALID_REQUEST", "Limit must be between 1 and 200"
                    )
                items, cursor = await source.list(resource, params, limit, cursor)
                return {"items": items, "next_cursor": cursor}
            except ValueError:
                return error(400, "INVALID_REQUEST", "Invalid filters, limit or cursor")

        return endpoint

    def detail_endpoint(resource):
        async def endpoint(id: str):
            doc = await source.get(resource, id)
            return (
                doc
                if doc is not None
                else error(404, "NOT_FOUND", "Resource not found")
            )

        return endpoint

    def singleton_endpoint(resource):
        async def endpoint(request: Request):
            try:
                return await source.singleton(resource, dict(request.query_params))
            except ValueError:
                return error(400, "INVALID_REQUEST", "Invalid singleton query")

        return endpoint

    for resource in LISTS:
        result.add_api_route(
            "/" + resource,
            list_endpoint(resource),
            methods=["GET"],
            name="list_" + resource,
        )
    for resource in ["incidents", "proposals", "payout-runs", "disputes"]:
        result.add_api_route(
            "/" + resource + "/{id}",
            detail_endpoint(resource),
            methods=["GET"],
            name="get_" + resource,
        )
    for resource in ["meta", "cash", "money-graph"]:
        result.add_api_route(
            "/" + resource,
            singleton_endpoint(resource),
            methods=["GET"],
            name="get_" + resource,
        )
    return result


router = build_router()
