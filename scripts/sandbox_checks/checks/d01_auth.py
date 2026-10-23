"""D1: record actual OAuth permissions without assuming sandbox capabilities."""

from ..runner import CheckContext, Step

# The official OAuth example uses /payments/payouts; the pending mock contract
# uses /services/payments/payouts. Retain the actual scope string as evidence.
SCOPES = {
    "payouts": {
        "https://uri.paypal.com/payments/payouts",
        "https://uri.paypal.com/services/payments/payouts",
    },
    "disputes_read": {"https://uri.paypal.com/services/disputes/read-seller"},
    "disputes_update_seller": {"https://uri.paypal.com/services/disputes/update-seller"},
    "disputes_create": {"https://uri.paypal.com/services/disputes/create"},
    "reporting": {"https://uri.paypal.com/services/reporting/search/read"},
}
REQUIRED = ("payouts", "disputes_read", "disputes_update_seller", "reporting")


async def authenticate(ctx: CheckContext) -> dict:
    info = await ctx.client.authenticate()
    return {"scope": info.scope, "expires_in": info.expires_in}


async def inspect_scopes(ctx: CheckContext) -> dict:
    scope = ctx.data["scope"]
    observed = set(scope.split())
    permissions = {name: bool(observed & values) for name, values in SCOPES.items()}
    missing = [name for name in REQUIRED if not permissions[name]]
    verdict = "BLOCKED" if missing else "CONFIRMED"
    for name, present in permissions.items():
        label = "PRESENT" if present else "MISSING"
        optional = (
            " (optional; UI dispute creation remains available)"
            if name == "disputes_create"
            else ""
        )
        ctx.note(f"{name}: {label}{optional}")
    finding = {
        "check_id": "D1",
        "verdict": verdict,
        "scope": scope,
        "permissions": permissions,
        "missing_required": missing,
        "observed_at": ctx.state["started_at"],
        "evidence_files": [
            path.relative_to(ctx.root).as_posix()
            for path in sorted(ctx.root.glob("????-??-??/D1/*_oauth_token.json"))
        ],
    }
    ctx.write_json(ctx.check_dir / "findings.json", finding)
    ctx.note(f"D1: {verdict}")
    return finding


def get_steps() -> list[Step]:
    return [Step("authenticate", authenticate), Step("inspect_scopes", inspect_scopes)]
