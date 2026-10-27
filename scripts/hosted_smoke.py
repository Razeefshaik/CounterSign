"""Exercise a disposable hosted demo through public HTTP only (resets its week).

Usage: python scripts/hosted_smoke.py https://YOUR-SERVICE.run.app
No environment files, credentials, database access or PayPal calls are used.
"""

import argparse
import sys
import time
from html.parser import HTMLParser
from urllib.parse import urlsplit

import httpx


class SmokeError(Exception):
    """A fixed, non-sensitive diagnostic suitable for a public smoke run."""


def require(condition, message):
    if not condition:
        raise SmokeError(message)


def usd(value, minor):
    return isinstance(value, dict) and type(value.get("minor")) is int and value == {
        "minor": minor, "currency": "USD"
    }


class Banner(HTMLParser):
    found = False

    def handle_starttag(self, tag, attrs):
        if ("id", "simulation") in attrs:
            self.found = True


class HostedSmoke:
    def __init__(self, client, *, timeout=300, pause=time.sleep, clock=time.monotonic):
        self.client, self.timeout, self.pause, self.clock = client, timeout, pause, clock

    def request(self, method, path, *, status=200, **kwargs):
        response = self.client.request(method, path, **kwargs)
        require(
            response.status_code == status, f"{method} {path.split('?')[0]}: expected HTTP {status}"
        )
        try:
            return response.json()
        except ValueError:
            raise SmokeError("Expected a JSON API response") from None

    def items(self, resource):
        result, cursor, seen = [], None, set()
        while True:
            params = {"limit": 200}
            if cursor:
                params["cursor"] = cursor
            page = self.request("GET", "/api/v1/" + resource, params=params)
            result.extend(page["items"])
            cursor = page["next_cursor"]
            if cursor is None:
                return result
            require(isinstance(cursor, str) and cursor not in seen, "Invalid pagination cursor")
            seen.add(cursor)
            require(len(seen) <= 100, "Pagination exceeded smoke limit")

    def idle(self):
        end = self.clock() + self.timeout
        while True:
            state = self.request("GET", "/api/v1/demo/state")
            if not state["running"]:
                require(
                    (state.get("last_result") or {}).get("ok") is not False,
                    "Demo command failed; inspect the hosted demo checkpoint",
                )
                return state
            require(self.clock() < end, "Timed out waiting for the staged demo command")
            self.pause(0.25)

    def run(self):
        meta = self.request("GET", "/api/v1/meta")
        require(
            meta.get("mode") == "demo"
            and meta.get("paypal_env") == "mock"
            and meta.get("simulated") is True,
            "Expected a simulated demo on mock PayPal",
        )
        require(
            meta.get("simulation_label") == "Simulated: mock PayPal", "Simulation label mismatch"
        )
        page = self.client.get("/")
        require(
            page.status_code == 200 and "text/html" in page.headers.get("content-type", ""),
            "Dashboard HTML did not load",
        )
        banner = Banner()
        banner.feed(page.text)
        require(banner.found, "Dashboard is missing id=simulation")
        self.idle()
        self.request("POST", "/api/v1/demo/reset", status=202)
        state = self.idle()
        require(
            state["step"] == 0 and state["total"] == 11 and not state["complete"],
            "Reset did not return an empty eleven-step week",
        )
        for step in range(1, 12):
            self.request("POST", "/api/v1/demo/step", status=202)
            state = self.idle()
            require(
                state["step"] == step and state["total"] == 11,
                f"Staged step {step} did not complete exactly once",
            )
            print(f"Staged step {step}/11 passed", flush=True)
        require(
            state["complete"] is True
            and len(state["steps"]) == 11
            and all(s["done"] for s in state["steps"]),
            "Demo week is incomplete",
        )

        runs = self.items("payout-runs")
        friday = [r for r in runs if usd(r["total"], 108600)]
        require(
            len(friday) == 1 and friday[0]["state"] == "DONE", "Expected one paid $1,086 Friday run"
        )
        detail = self.request("GET", "/api/v1/payout-runs/" + friday[0]["id"])
        lines = list(detail["lines"])
        for replacement in detail["replacements"]:
            previous = self.request("GET", "/api/v1/payout-runs/" + replacement["from_run_id"])
            matches = [line for line in previous["lines"] if line["id"] == replacement["line_id"]]
            require(len(matches) == 1, "Friday replacement line is unavailable")
            lines.extend(matches)
        require(len({line["id"] for line in lines}) == len(lines), "Duplicate Friday line")
        require(
            all(usd(line["amount"], line["amount"]["minor"]) for line in lines)
            and sum(line["amount"]["minor"] for line in lines) == 108600,
            "Friday lines do not total $1,086",
        )
        item_ids = []
        for line in lines:
            successes = [a for a in line["attempts"] if a["state"] == "SUCCESS"]
            require(
                line["state"] == "PAID" and len(successes) == 1,
                "A Friday line was not paid exactly once",
            )
            require(
                successes[0]["amount"] == line["amount"] and successes[0]["paypal_item_id"],
                "Successful Friday item amount or identity mismatch",
            )
            item_ids.append(successes[0]["paypal_item_id"])
        require(len(set(item_ids)) == len(item_ids), "Friday lines reuse a payout item")
        require((detail.get("approval") or {}).get("used_at"), "Friday approval was not consumed")
        cash = self.request("GET", "/api/v1/cash")
        require(usd(cash["paypal"]["available"], 35400), "Final available cash is not $354")
        makers = {m["id"]: m["display_name"] for m in self.items("makers")}
        disputes = {d["id"]: d for d in self.items("disputes")}
        reserves = [
            r
            for r in self.items("reservations")
            if r["kind"] == "DISPUTE_EARMARK" and r["state"] == "ACTIVE"
        ]
        b = [
            r
            for r in reserves
            if makers.get(disputes[r["dispute_id"]]["order"]["maker_id"]) == "Maker B"
        ]
        require(
            len(b) == 1 and usd(b[0]["amount"], 5400) and b[0]["affects_cash"] is False,
            "Maker B's $54 non-cash reserve is missing",
        )
        attacks = self.request("GET", "/api/v1/playground/attacks")["items"]
        require(
            len(attacks) == 4
            and {a["id"] for a in attacks}
            == {
                "refund_to_other_account",
                "retry_settled_payout",
                "refund_without_owner",
                "custom",
            },
            "Expected the four shield playground attacks",
        )
        attempt = self.request(
            "POST", "/api/v1/playground/attempts", json={"attack": "refund_to_other_account"}
        )
        require(
            attempt.get("verdict") == "BLOCK" and attempt.get("written") is False,
            "Playground did not block without writing",
        )
        require(
            self.request("GET", "/api/v1/demo/state")["complete"] is True, "Demo no longer complete"
        )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("base_url")
    parser.add_argument(
        "--timeout", type=int, default=300, help="Maximum seconds per staged command"
    )
    args = parser.parse_args(argv)
    url = urlsplit(args.base_url)
    if (
        url.scheme not in {"http", "https"}
        or not url.hostname
        or url.username
        or url.password
        or url.query
        or url.fragment
        or url.path not in {"", "/"}
        or args.timeout <= 0
    ):
        parser.error("An HTTP(S) origin without credentials and a positive timeout are required")
    try:
        with httpx.Client(
            base_url=args.base_url.rstrip("/"), timeout=30, trust_env=False, follow_redirects=False
        ) as client:
            HostedSmoke(client, timeout=args.timeout).run()
    except (SmokeError, httpx.HTTPError, KeyError, TypeError, ValueError):
        error = sys.exception()
        print(
            "Hosted smoke FAILED: "
            + (
                str(error)
                if isinstance(error, SmokeError)
                else "HTTP failure or unexpected API shape"
            ),
            file=sys.stderr,
        )
        return 1
    print(
        "Hosted smoke passed: 11 steps, Friday paid once, cash $354, B reserve $54, playground BLOCK"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
