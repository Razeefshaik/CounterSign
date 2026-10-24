"""Staged sandbox actions for scripts/demo controls; never agent tools."""

from countersign.paypal.http import PayPalHTTP, resource_id


class SandboxControls:
    def __init__(self, http: PayPalHTTP):
        self.http = http

    async def require_evidence(self, dispute_id: str, body: dict):
        return await self.http.request(
            "POST", f"/v1/customer/disputes/{resource_id(dispute_id)}/require-evidence", json=body
        )

    async def adjudicate(self, dispute_id: str, body: dict):
        return await self.http.request(
            "POST", f"/v1/customer/disputes/{resource_id(dispute_id)}/adjudicate", json=body
        )
