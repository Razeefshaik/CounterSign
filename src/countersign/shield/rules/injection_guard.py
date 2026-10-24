"""Block financial proposals carrying an observed prompt-injection flag.

Detection belongs to the commander; this pure rule only checks the supplied
flag. Harmless waiting, drafting and owner requests remain available.
"""

from countersign.shield.engine import RuleName, RuleResult, blocked, passed
from countersign.shield.snapshot import NON_FINANCIAL, Proposal, Snapshot

NAME = RuleName.INJECTION_GUARD


def check(proposal: Proposal, snap: Snapshot) -> RuleResult | None:
    if proposal.action in NON_FINANCIAL:
        return None
    if any(flag.get("type") == "PROMPT_INJECTION" for flag in proposal.flags):
        return blocked(NAME, ["prompt injection flagged; financial action refused"])
    return passed(NAME, "no prompt-injection flag on the financial proposal")
