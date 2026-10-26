"""Bounded read/investigate/propose loop; never writes or evaluates permission."""

import asyncio
from collections.abc import Mapping

from countersign.agent.models import ModelError, create_model
from countersign.agent.params import ProposalParamsError
from countersign.agent.prompts import SYSTEM_PROMPT
from countersign.agent.proposal import ProposalError, StructuredProposal, parse_proposal
from countersign.agent.safety import injection_flags, sanitize
from countersign.agent.tools import RECOVERABLE_TOOL_ERRORS, ToolError


class CommanderError(RuntimeError):
    """A fixed error that cannot contain buyer, credential or model text."""


class Commander:
    def __init__(self, model, tools, *, max_rounds=8, max_tool_calls=16,
                 max_failed_tool_calls=3, timeout=60, secrets=()):
        if (
            type(max_rounds) is not int
            or not 1 <= max_rounds <= 32
            or type(max_tool_calls) is not int
            or not 1 <= max_tool_calls <= 64
            or type(max_failed_tool_calls) is not int
            or not 0 <= max_failed_tool_calls <= 16
            or type(timeout) not in (int, float)
            or not 0 < timeout <= 300
        ):
            raise CommanderError("INVALID_COMMANDER_LIMITS")
        self.model = create_model(model)
        self.tools = tools
        self.max_rounds, self.max_tool_calls, self.timeout = max_rounds, max_tool_calls, timeout
        self.max_failed_tool_calls = max_failed_tool_calls
        model_key = getattr(getattr(self.model, "config", None), "api_key", None)
        self._secrets = tuple(secrets) + ((model_key,) if model_key else ())
        self._lock = asyncio.Lock()

    def __repr__(self):
        return "Commander(values=<REDACTED>)"

    @property
    def model_id(self):
        value = getattr(self.model, "model_id", "injected")
        if (
            type(value) is not str
            or not 1 <= len(value) <= 200
            or value.strip() != value
            or any(ord(character) < 32 or ord(character) == 127 for character in value)
        ):
            raise CommanderError("INVALID_MODEL_ID")
        return sanitize(value, self._secrets)

    async def step(self, incident: Mapping, context: Mapping | None = None) -> StructuredProposal:
        """Return one validated proposal, carrying flags from all observed data."""
        try:
            async with self._lock:
                try:
                    async with asyncio.timeout(self.timeout):
                        return await self._step(incident, context)
                finally:
                    finish = getattr(self.model, "finish_step", None)
                    if callable(finish):
                        finish()
        except (CommanderError, ProposalError):
            raise
        except TimeoutError:
            raise CommanderError("COMMANDER_TIMEOUT") from None
        except ModelError as error:
            if error.args == ("MODEL_QUOTA_EXHAUSTED",):
                raise CommanderError("MODEL_QUOTA_EXHAUSTED") from None
            raise CommanderError("COMMANDER_STEP_FAILED") from None
        except Exception:  # noqa: BLE001 - discard private provider/tool exception text.
            raise CommanderError("COMMANDER_STEP_FAILED") from None

    async def _step(self, incident, context):
        if (
            not isinstance(incident, Mapping)
            or type(incident.get("id")) is not str
            or not incident["id"]
        ):
            raise CommanderError("INVALID_INCIDENT")
        identifier = incident["id"]
        if context is not None and not isinstance(context, Mapping):
            raise CommanderError("INVALID_INCIDENT_CONTEXT")
        source = {"incident": dict(incident), "context": dict(context or {})}
        flags = list(injection_flags(source, source="input", secrets=self._secrets))
        data = sanitize(source, self._secrets)
        if data["incident"]["id"] != identifier:
            raise CommanderError("INVALID_INCIDENT")
        observations, seen_calls = [], set()
        failed_calls = 0
        invalid_proposals = 0
        for _ in range(self.max_rounds):
            request = {
                "system": SYSTEM_PROMPT,
                "data": data,
                "tools": self.tools.schemas,
                "observations": observations,
            }
            # The caller and all model implementations see only redacted data.
            answer = await self.model.complete(sanitize(request, self._secrets))
            if type(answer) is not dict:
                raise CommanderError("INVALID_MODEL_RESPONSE")
            if set(answer) == {"proposal_json"} or "tool_calls" not in answer:
                try:
                    return parse_proposal(
                        answer["proposal_json"] if set(answer) == {"proposal_json"} else answer,
                        incident_id=identifier, flags=flags, secrets=self._secrets,
                    )
                except ProposalParamsError as error:
                    invalid_proposals += 1
                    if invalid_proposals >= 2:
                        raise CommanderError("COMMANDER_PROPOSAL_INVALID") from None
                    flags.extend(injection_flags(answer, source="invalid_proposal",
                                                 secrets=self._secrets))
                    observations.append({"error": "INVALID_PROPOSAL_PARAMS", "action": error.action})
                    continue
            calls = answer["tool_calls"]
            if set(answer) != {"tool_calls"} or type(calls) is not list or not 1 <= len(calls) <= 4:
                raise CommanderError("INVALID_MODEL_TOOL_CALLS")
            pending = []
            for call in calls:
                if (
                    type(call) is not dict
                    or set(call) != {"id", "name", "args"}
                    or type(call["id"]) is not str
                    or not call["id"]
                    or len(call["id"]) > 128
                    or type(call["name"]) is not str
                    or type(call["args"]) is not dict
                    or call["id"] in seen_calls
                ):
                    raise CommanderError("INVALID_MODEL_TOOL_CALLS")
                seen_calls.add(call["id"])
                if len(seen_calls) > self.max_tool_calls:
                    raise CommanderError("COMMANDER_TOOL_BUDGET_EXCEEDED")
                pending.append(sanitize(call, self._secrets))
            for call in pending:
                try:
                    result = await self.tools.invoke(call["name"], call["args"])
                except ToolError as error:
                    code = error.args[0]
                    if code not in RECOVERABLE_TOOL_ERRORS:
                        raise CommanderError("COMMANDER_TOOL_FAILED") from None
                    failed_calls += 1
                    if failed_calls > self.max_failed_tool_calls:
                        raise CommanderError("COMMANDER_TOOL_BUDGET_EXCEEDED") from None
                    result = {"error": code, "tool": call["name"]}
                except Exception:  # noqa: BLE001 - tool errors may contain credentials or buyer text.
                    raise CommanderError("COMMANDER_TOOL_FAILED") from None
                flags.extend(
                    injection_flags(result, source=f"tool:{call['name']}", secrets=self._secrets)
                )
                observations.append(
                    {"tool_call": call, "untrusted_result": sanitize(result, self._secrets)}
                )
        raise CommanderError("COMMANDER_BUDGET_EXHAUSTED")
