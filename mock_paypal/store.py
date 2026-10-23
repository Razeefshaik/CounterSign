"""Per-app state, resettable without persistence or external services."""

import random
import secrets
from datetime import timedelta

from mock_paypal.clock import MockClock
from mock_paypal.config import MockConfig

TOKEN_LIFETIME_SECONDS = 32400
# pending D1: override through create_app(oauth_scope=...) after sandbox findings.
DEFAULT_SCOPE = (
    "https://uri.paypal.com/services/payments/payouts "
    "https://uri.paypal.com/services/disputes/read-seller "
    "https://uri.paypal.com/services/disputes/update-seller "
    "https://uri.paypal.com/services/reporting/search/read"
)


class MockStore:
    def __init__(self, seed: int = 17):
        self.reset(seed)

    def reset(self, seed: int = 17) -> None:
        self.seed = seed
        self.random = random.Random(seed)
        self.clock = MockClock()
        self.config = MockConfig()
        self.tokens = {}
        self.records = {}
        self._ids = 0

    def new_id(self, prefix: str = "MOCK") -> str:
        self._ids += 1
        return f"{prefix}{self._ids:08X}{self.random.getrandbits(48):012X}"

    def issue_token(self) -> str:
        # Do not derive bearer tokens from the public reset seed.
        token = "MOCK-" + secrets.token_urlsafe(32)
        self.tokens[token] = self.clock.now + timedelta(seconds=TOKEN_LIFETIME_SECONDS)
        return token

    def valid_token(self, token: str) -> bool:
        expiry = self.tokens.get(token)
        if expiry is None:
            return False
        if self.clock.now >= expiry:
            del self.tokens[token]
            return False
        return True
