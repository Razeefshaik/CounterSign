"""Owner-operated, one-call model smoke test with one harmless read-only stub.

Run with ``python -m scripts.llm_smoke`` after setting the three model variables.
Only an explicit --env-file loads a file, at runtime. This module opens no
connections on import and never invokes PayPal or executes a returned tool call.
"""

import argparse
import asyncio
import contextlib
import json
import logging
import os
import sys
from collections.abc import Mapping

from countersign.agent.models import ChatModelConfig, LangChainModel, ModelError
from countersign.agent.runtime import load_chat_model_config

_PUBLIC_ERRORS = frozenset(
    {
        "MODEL_PROVIDER_AND_ID_REQUIRED",
        "MODEL_PROVIDER_UNSUPPORTED",
        "MODEL_API_KEY_REQUIRED",
        "MODEL_CONFIGURATION_INVALID",
        "MODEL_CONFIGURATION_REQUIRED",
        "MODEL_DEPENDENCIES_REQUIRED",
        "MODEL_CALL_FAILED",
        "INVALID_MODEL_RESPONSE",
        "MODEL_SMOKE_FAILED",
    }
)
_READ_TOOL = {
    "type": "function",
    "function": {
        "name": "read_demo_status",
        "description": "Read the fixed demo status. This stub has no network, database or PayPal access.",
        "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
    },
}


def _public_code(exc):
    code = str(exc) if isinstance(exc, ModelError) else ""
    return code if code in _PUBLIC_ERRORS else "MODEL_SMOKE_FAILED"


@contextlib.contextmanager
def _quiet_provider():
    """Provider debugging and incidental prints are not public smoke evidence."""
    previous = logging.root.manager.disable
    try:
        logging.disable(logging.CRITICAL)
        with (
            open(os.devnull, "w", encoding="utf-8") as destination,
            contextlib.redirect_stdout(destination),
            contextlib.redirect_stderr(destination),
        ):
            yield
    finally:
        logging.disable(previous)


async def smoke(config, *, factory=None):
    """Make exactly one complete()/ainvoke() call and retain only safe metadata."""
    if not isinstance(config, ChatModelConfig):
        raise ModelError("MODEL_CONFIGURATION_REQUIRED")
    # Revalidate public identity fields too: a direct caller must not make the
    # provider/model labels another route for printing the API key.
    config = load_chat_model_config(
        {
            "COUNTERSIGN_LLM_PROVIDER": config.provider,
            "COUNTERSIGN_LLM_MODEL": config.model,
            "GOOGLE_API_KEY": config.api_key,
        }
    )
    try:
        with _quiet_provider():
            model = LangChainModel(config, factory=factory)
            try:
                result = await model.complete(
                    {
                        "system": "Use the harmless read_demo_status tool once with no arguments.",
                        "data": {"task": "Check the local demonstration status."},
                        "observations": [],
                        "tools": [_READ_TOOL],
                    }
                )
            finally:
                await model.aclose()
    except Exception as exc:  # noqa: BLE001 - neither private exceptions nor raw responses escape.
        raise ModelError(_public_code(exc)) from None
    output = {
        "provider": config.provider,
        "model": config.model,
        "tool_call_returned": bool(result.get("tool_calls")),
    }
    usage = getattr(model, "last_usage_metadata", None)
    if isinstance(usage, Mapping):
        counts = {
            name: usage[name]
            for name in ("input_tokens", "output_tokens", "total_tokens")
            if type(usage.get(name)) is int and usage[name] >= 0
        }
        if counts:
            output["tokens"] = counts
    return output


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", help="Explicit runtime environment file; existing values win")
    args = parser.parse_args(argv)
    try:
        if args.env_file is not None:
            from dotenv import load_dotenv

            load_dotenv(args.env_file, override=False)
        config = load_chat_model_config()
        if config is None:
            print(
                "No model configured; set COUNTERSIGN_LLM_PROVIDER, "
                "COUNTERSIGN_LLM_MODEL and GOOGLE_API_KEY."
            )
            return 1
        with asyncio.Runner(
            loop_factory=asyncio.SelectorEventLoop if sys.platform == "win32" else None
        ) as runner:
            result = runner.run(smoke(config))
        print(json.dumps(result, sort_keys=True))
    except KeyboardInterrupt:
        return 130
    except Exception as exc:  # noqa: BLE001 - no provider, file or settings exception text is printed.
        print(_public_code(exc))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
