"""C65: read-only demo proxy. The browser cannot contact the mock control surface."""

import re
from datetime import datetime

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from mock_paypal.call_routes import PRODUCTS, ROUTES

SOURCE = "Simulated: PayPal mock"
FIELDS = {"id", "at", "source", "method", "route", "status", "product", "direction",
          "request_id_hash", "sender_batch_id_hash", "error", "original_batch", "incomplete"}


def public_document(value):
    """Reject upstream additions rather than accidentally proxying private fields."""
    if not isinstance(value, dict) or set(value) != {"source", "limit", "total", "counts", "rows"}:
        raise ValueError("INVALID_CALL_LOG")
    if value["source"] != SOURCE or value["limit"] != 500 or type(value["total"]) is not int or value["total"] < 0:
        raise ValueError("INVALID_CALL_LOG")
    counts = value["counts"]
    if not isinstance(counts, dict) or set(counts) - {*PRODUCTS, "Other"} or any(type(v) is not int or v < 0 for v in counts.values()) or sum(counts.values()) != value["total"]:
        raise ValueError("INVALID_CALL_LOG")
    rows = value["rows"]
    if not isinstance(rows, list) or len(rows) != min(500, value["total"]):
        raise ValueError("INVALID_CALL_LOG")
    previous = value["total"] + 1
    for row in rows:
        if not isinstance(row, dict) or set(row) != FIELDS or row["source"] != SOURCE:
            raise ValueError("INVALID_CALL_LOG")
        if type(row["id"]) is not int or row["id"] != previous - 1:
            raise ValueError("INVALID_CALL_LOG")
        previous = row["id"]
        now = datetime.fromisoformat(row["at"])
        if now.tzinfo is None or row["at"] != now.isoformat().replace("+00:00", "Z"):
            raise ValueError("INVALID_CALL_LOG")
        if row["method"] not in {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS", "OTHER"}:
            raise ValueError("INVALID_CALL_LOG")
        if row["direction"] not in {"App to PayPal", "PayPal to app"}:
            raise ValueError("INVALID_CALL_LOG")
        allowed = ROUTES.get(row["product"], ())
        if row["direction"] == "PayPal to app":
            allowed = ("/webhooks/paypal",) if row["product"] == "Webhooks" else ()
        elif row["product"] == "Other":
            allowed = ("/v1/{unmatched}", "/v2/{unmatched}")
        if row["route"] not in allowed or row["status"] is not None and (type(row["status"]) is not int or not 100 <= row["status"] <= 599):
            raise ValueError("INVALID_CALL_LOG")
        if any(row[key] is not None and not re.fullmatch(r"[a-f0-9]{12}", row[key]) for key in ("request_id_hash", "sender_batch_id_hash")):
            raise ValueError("INVALID_CALL_LOG")
        if row["error"] not in {None, "USER_BUSINESS_ERROR", "DUPLICATE_SENDER_BATCH_ID", "PREVIOUS_REQUEST_IN_PROGRESS"} or any(type(row[key]) is not bool for key in ("original_batch", "incomplete")):
            raise ValueError("INVALID_CALL_LOG")
    return value


def build_router(runtime):
    result = APIRouter()
    @result.get("/demo/paypal-calls")
    async def calls(request: Request):
        from countersign.api.dashboard import demo_bar_allowed
        if not demo_bar_allowed(runtime):
            return JSONResponse({"error":{"code":"NOT_FOUND", "message":"Resource not found"}}, status_code=404)
        if request.url.query:
            return JSONResponse({"error":{"code":"INVALID_REQUEST", "message":"Call log takes no parameters"}}, status_code=422)
        try:
            async with httpx.AsyncClient(timeout=3, trust_env=False, follow_redirects=False) as client:
                response = await client.get(runtime.paypal.base_url + "/_mock/paypal-calls")
                response.raise_for_status()
                return public_document(response.json())
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            return JSONResponse({"error":{"code":"DATA_UNAVAILABLE", "message":"Mock call log unavailable"}}, status_code=503)
    return result
