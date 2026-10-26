"""Explicit runtime model settings; imports never read files or environment values."""

import os
import re
from collections.abc import Mapping

from countersign.agent.models import ChatModelConfig, ModelError

_MODEL_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,199}\Z")
DEFAULT_GEMINI_MODEL = "gemini-3.5-flash"


def load_chat_model_config(environment=None) -> ChatModelConfig | None:
    """An absent configuration stays disabled; an incomplete one never falls back.

    Gemini uses its provider's GOOGLE_API_KEY rather than another copy of that
    secret under an application-specific name. No values enter diagnostics.
    """
    source = os.environ if environment is None else environment
    if not isinstance(source, Mapping):
        raise ModelError("MODEL_CONFIGURATION_INVALID")
    try:
        values = [
            source.get(name, "")
            for name in ("COUNTERSIGN_LLM_PROVIDER", "COUNTERSIGN_LLM_MODEL", "GOOGLE_API_KEY")
        ]
        if any(type(value) is not str for value in values):
            raise ValueError
        provider, model, key = (value.strip() for value in values)
    except Exception:  # noqa: BLE001 - even a custom settings mapping can carry private text.
        raise ModelError("MODEL_CONFIGURATION_INVALID") from None
    if not any((provider, model, key)):
        return None
    if not provider:
        raise ModelError("MODEL_PROVIDER_AND_ID_REQUIRED")
    if provider != "google_genai":
        raise ModelError("MODEL_PROVIDER_UNSUPPORTED")
    if not key:
        raise ModelError("MODEL_API_KEY_REQUIRED")
    model = model or DEFAULT_GEMINI_MODEL
    if (
        not _MODEL_ID.fullmatch(model)
        or any(ord(character) < 32 or ord(character) == 127 for character in key)
        or key.casefold() in provider.casefold()
        or key.casefold() in model.casefold()
    ):
        raise ModelError("MODEL_CONFIGURATION_INVALID")
    config = ChatModelConfig(provider=provider, model=model, api_key=key)
    config.validate()
    return config
