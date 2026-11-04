"""Local, explicit one-variable loader. Never populate the process environment."""

import io
import re

from countersign.agent.models import ChatModelConfig, ModelError

KEY_NAME = "OPEN_ROUTER_API_KEY"
MODEL_ID = "google/gemini-3.5-flash"


def openrouter_config(path, *, model=MODEL_ID):
    """Parse only matching physical lines; unrelated settings are never loaded.

    No expansion, multiline values, duplicates or environment exports. Callers
    receive a repr-redacted config whose key is passed only to the model client.
    """
    from dotenv import dotenv_values

    found = []
    try:
        with open(path, encoding="utf-8-sig") as stream:
            for line in stream:
                if not re.match(r"^\s*(?:export\s+)?OPEN_ROUTER_API_KEY\s*=", line):
                    continue
                values = dotenv_values(stream=io.StringIO(line), interpolate=False)
                key = values.get(KEY_NAME)
                if not isinstance(key, str) or not key.strip() or len(key) > 4096:
                    raise ValueError
                if any(c.isspace() or c in "$\r\n" for c in key):
                    raise ValueError
                found.append(key)
        if len(found) != 1:
            raise ValueError
    except Exception:  # noqa: BLE001 - file/parser errors must not expose values or paths.
        raise ModelError("MODEL_API_KEY_REQUIRED") from None
    return ChatModelConfig("openrouter", model, found[0])
