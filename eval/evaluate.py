"""M18: checkpointed mock evaluation; this module never reads an environment file.

python -m eval.evaluate --model none --k 5 --output eval/results/model-off
Configured mode is owner-operated: the same explicit model drives both agents.
Only generated, disposable PostgreSQL databases are migrated or dropped here.
"""

import argparse
import asyncio
import hashlib
import json
import os
import re
import sys
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from eval.metrics import INVARIANTS
from eval.report import write_results
from eval.runner import QUOTA_EXHAUSTED, ModelQuotaExhausted, run_one
from eval.validate import validate

ROOT = Path(__file__).resolve().parents[1]


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def code_identity():
    """Bind outcome-producing code/fixtures without reading credentials or results."""
    paths = {
        ROOT / "eval" / filename
        for filename in [
            "evaluate.py",
            "runner.py",
            "report.py",
            "metrics.py",
            "owner.py",
            "validate.py",
            "tcp_mock.py",
        ]
    }
    for folder in ["eval/systems", "src/countersign", "mock_paypal", "migrations"]:
        paths.update(
            path
            for path in (ROOT / folder).rglob("*")
            if path.is_file() and path.suffix in {".py", ".json"}
        )
    return digest(
        {
            path.relative_to(ROOT).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(paths)
            if path.is_file()
        }
    )


def atomic_json(path, value):
    """A completed cell is durable before the next mock is constructed."""
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, sort_keys=True, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def load_scenarios(paths=None):
    """Sandbox fixtures belong exclusively to C26, even when stored beside M14."""
    selected = (
        [Path(path) for path in paths]
        if paths
        else sorted((ROOT / "eval/scenarios").glob("*.json"))
    )
    documents = [json.loads(path.read_text(encoding="utf-8")) for path in selected]
    for document in documents:
        validate(document)
    if paths and any(document["ground"] != "mock" for document in documents):
        raise ValueError("Explicit sandbox scenarios cannot run in the mock benchmark")
    return [document for document in documents if document["ground"] == "mock"]


@contextmanager
def disposable_database(admin_url):
    """Create a fresh database; never migrate/reset the administrator's database."""
    import psycopg
    from alembic import command
    from alembic.config import Config
    from psycopg import sql
    from psycopg.conninfo import make_conninfo
    from sqlalchemy import create_engine

    name = "cs_m18_" + uuid4().hex + "_eval"
    with psycopg.connect(admin_url, autocommit=True) as admin:
        if admin.info.server_version // 10000 != 16:
            raise ValueError("PostgreSQL 16 is required")
        admin.execute(sql.SQL("CREATE DATABASE {} TEMPLATE template0").format(sql.Identifier(name)))
    conninfo = make_conninfo(admin_url, dbname=name)
    try:
        engine = create_engine("postgresql+psycopg://", creator=lambda: psycopg.connect(conninfo))
        try:
            with engine.begin() as connection:
                config = Config(str(ROOT / "migrations/alembic.ini"))
                config.attributes["connection"] = connection
                command.upgrade(config, "head")
        finally:
            engine.dispose()
        yield conninfo
    finally:
        assert re.fullmatch(r"cs_m18_[0-9a-f]{32}_eval", name)
        with psycopg.connect(admin_url, autocommit=True) as admin:
            admin.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))


def _factory_defaults(database_url, configured):
    from eval.systems.countersign import CountersignSystem
    from eval.systems.rules_only import RulesOnlySystem

    factories = {"rules_only": RulesOnlySystem}
    if configured:
        from eval.systems.baseline_llm import BaselineLLMSystem

        factories["baseline_llm"] = BaselineLLMSystem
    factories["countersign"] = lambda: CountersignSystem(database_url=database_url)
    return factories


def _finish(rows, output, k, mode, quota_stops):
    write_results(rows, output, k)
    lines = [
        "",
        "## Detailed mock results",
        "",
        "| System | Owner interruptions | Model calls | Input tokens | Output tokens | Error rows |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for system in sorted({row["system"] for row in rows}):
        selected = [row for row in rows if row["system"] == system]
        totals = [
            sum(row["owner_escalations"] for row in selected),
            sum(row["llm_calls"] for row in selected),
            sum(row["tokens"]["in"] for row in selected),
            sum(row["tokens"]["out"] for row in selected),
            sum(row["error"] is not None for row in selected),
        ]
        lines.append("| " + " | ".join([system, *map(str, totals)]) + " |")
    lines += ["", "| System | Invariant | Observed violations |", "| --- | --- | ---: |"]
    for system in sorted({row["system"] for row in rows}):
        for invariant in INVARIANTS:
            total = sum(row["invariants"][invariant] for row in rows if row["system"] == system)
            lines.append(f"| {system} | {invariant} | {total} |")
    lines += [
        "",
        "pass^k is the proportion of scenarios passing all k repetitions; no independence assumption is made.",
        "A zero counter on an errored run does not establish safety. These tests are not a financial audit or proof of safety.",
        "Baseline systems use PayPal idempotency; the comparison does not disable it to manufacture failures.",
    ]
    if mode == "none":
        lines += [
            "CounterSign's model was disabled. This measures deterministic paths, not AI reasoning; baseline_llm was not run.",
            "Repeated deterministic runs are reproducibility checks, not independent model trials.",
        ]
    else:
        lines.append(
            "Both model-based systems used the same explicitly configured model; rules_only used no model."
        )
    lines += [
        f"Quota-stopped attempts retried: {len(quota_stops)}. Completed-cell tables exclude these abandoned attempts.",
        (
            "Stopped-attempt measured usage: "
            f"{sum(row['llm_calls'] for row in quota_stops)} calls, "
            f"{sum(row['tokens']['in'] for row in quota_stops)} input tokens, "
            f"{sum(row['tokens']['out'] for row in quota_stops)} output tokens. "
            "Provider usage on a rejected/failed call may be unavailable; these are not billing totals."
        ),
    ]
    with (output / "report.md").open("a", encoding="utf-8") as stream:
        stream.write("\n".join(lines) + "\n")


async def evaluate(
    scenarios,
    *,
    output,
    k=5,
    model="none",
    database_url=None,
    llm=None,
    model_id=None,
    provider="google_genai",
    run_id=None,
    resume=False,
    factories=None,
    runner=run_one,
):
    """Run complete fresh-mock cells; quota-stopped cells are retried on resume.

    Checkpoints contain only public metrics, model ID and scenario hashes. The
    connection URL and API key are never serialized. A changed corpus/config is
    refused instead of combining incomparable experiments.
    """
    if model not in {"none", "configured"} or type(k) is not int or k < 1 or not scenarios:
        raise ValueError("A model mode, positive k and mock scenarios are required")
    for scenario in scenarios:
        validate(scenario)
        if scenario["ground"] != "mock":
            raise ValueError("Only mock scenarios can enter M18")
    if len({scenario["id"] for scenario in scenarios}) != len(scenarios):
        raise ValueError("Duplicate scenario IDs")
    configured = model == "configured"
    if configured:
        model_id = model_id or getattr(llm, "model_id", None)
        if not callable(getattr(llm, "complete", None)) or not isinstance(model_id, str):
            raise ValueError("An explicit measurable model and ID are required")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,199}", model_id):
            raise ValueError("Invalid model ID")
    elif llm is not None:
        raise ValueError("Model-disabled evaluation cannot receive a model")
    else:
        model_id = "none"
    if factories is None and not database_url:
        raise ValueError("A disposable migrated evaluation database is required")
    factories = factories if factories is not None else _factory_defaults(database_url, configured)
    expected_systems = (
        {"rules_only", "countersign", "baseline_llm"}
        if configured
        else {"rules_only", "countersign"}
    )
    if set(factories) != expected_systems:
        raise ValueError("Every system required by this model mode must run")
    manifest = {
        "version": 1,
        "ground": "mock",
        "mode": model,
        "model": model_id,
        "provider": provider if configured else "none",
        "k": k,
        "systems": sorted(factories),
        "scenarios": {scenario["id"]: digest(scenario) for scenario in scenarios},
        "scenario_order": [scenario["id"] for scenario in scenarios],
        "code": code_identity(),
    }
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    lock = output / ".evaluation.lock"
    # A crashed process leaves this visible; remove only after verifying it is dead.
    descriptor = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
    os.close(descriptor)
    try:
        checkpoint = output / "checkpoint.json"
        if resume:
            state = json.loads(checkpoint.read_text(encoding="utf-8"))
            if state["manifest"] != manifest or (run_id is not None and state["run_id"] != run_id):
                raise ValueError("Checkpoint does not match this evaluation")
        else:
            if any(path.name != lock.name for path in output.iterdir()):
                raise ValueError("Output exists; use --resume or choose a fresh output")
            state = {
                "manifest": manifest,
                "run_id": run_id or datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ"),
                "rows": [],
                "quota_stops": [],
            }
            atomic_json(checkpoint, state)
        run_id = state["run_id"]
        cells = {
            (system, scenario["id"], index): scenario
            for system in factories
            for scenario in scenarios
            for index in range(k)
        }
        completed = {}

        def validate_row(row):
            cell = (row["system"], row["scenario_id"], row["k"])
            if cell not in cells:
                raise ValueError("Unknown checkpoint cell")
            scenario = cells[cell]
            cell_model = model_id if configured and cell[0] != "rules_only" else "none"
            if (
                row["ground"] != "mock"
                or row["run_id"] != run_id
                or row["seed"] != scenario["seed"]
                or row["model"] != cell_model
                or row["provenance"] != scenario["provenance"]
                or row["fault_class"] != scenario["fault_class"]
            ):
                raise ValueError("Invalid checkpoint row")
            return cell

        for row in state["rows"]:
            cell = validate_row(row)
            if cell in completed or row["error"] == QUOTA_EXHAUSTED:
                raise ValueError("Invalid checkpoint cell")
            completed[cell] = row
        for row in state["quota_stops"]:
            validate_row(row)
            if row["error"] != QUOTA_EXHAUSTED:
                raise ValueError("Invalid quota attempt")
        for cell, scenario in cells.items():
            if cell in completed:
                continue
            system, _, index = cell
            system_model = llm if configured and system != "rules_only" else None
            row = await runner(
                scenario,
                factories[system](),
                run_id=run_id,
                k_index=index,
                model=model_id if system_model is not None else "none",
                llm=system_model,
                mock_transport="tcp" if configured else "asgi",
            )
            if validate_row(row) != cell:
                raise ValueError("Runner returned the wrong evaluation cell")
            if row["error"] == QUOTA_EXHAUSTED:
                state["quota_stops"].append(row)
                atomic_json(checkpoint, state)
                (output / "runs.partial.jsonl").write_text(
                    "".join(
                        json.dumps(item, sort_keys=True) + "\n" for item in [*state["rows"], row]
                    ),
                    encoding="utf-8",
                )
                raise ModelQuotaExhausted()
            state["rows"].append(row)
            atomic_json(checkpoint, state)
        _finish(state["rows"], output, k, model, state["quota_stops"])
        (output / "runs.partial.jsonl").unlink(missing_ok=True)
        return state["rows"]
    finally:
        lock.unlink()


async def _main(args):
    scenarios = load_scenarios(args.paths)
    llm, config = None, None
    if args.model == "configured":
        from countersign.agent.models import LangChainModel
        from countersign.agent.runtime import load_chat_model_config

        # Do not pass the whole process environment or open .env.
        names = ("COUNTERSIGN_LLM_PROVIDER", "COUNTERSIGN_LLM_MODEL", "GOOGLE_API_KEY")
        config = load_chat_model_config(
            {name: os.environ[name] for name in names if name in os.environ}
        )
        if config is None:
            raise ValueError("Explicit model configuration is required")
        llm = LangChainModel(config)
    try:
        with disposable_database(args.database_admin_url) as database_url:
            return await evaluate(
                scenarios,
                output=args.output,
                k=args.k,
                model=args.model,
                database_url=database_url,
                llm=llm,
                model_id=config.model if config else None,
                provider=config.provider if config else "none",
                resume=args.resume,
            )
    finally:
        if llm is not None:
            await llm.aclose()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="*")
    parser.add_argument("--model", choices=["none", "configured"], required=True)
    parser.add_argument("--k", type=int, default=5)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument(
        "--database-admin-url", default=os.environ.get("COUNTERSIGN_TEST_DATABASE_URL")
    )
    args = parser.parse_args(argv)
    if not args.database_admin_url:
        parser.error("Set COUNTERSIGN_TEST_DATABASE_URL or --database-admin-url")
    try:
        with asyncio.Runner(
            loop_factory=asyncio.SelectorEventLoop
            if sys.platform == "win32"
            else asyncio.new_event_loop
        ) as runner:
            rows = runner.run(_main(args))
    except ModelQuotaExhausted:
        print("MODEL_QUOTA_EXHAUSTED: checkpoint saved; resume the same command with --resume.")
        return 3
    except Exception:  # noqa: BLE001 -- no driver/provider exception may expose private values
        print("Evaluation failed; inspect configuration and checkpoint. No private values printed.")
        return 1
    for system in sorted({row["system"] for row in rows}):
        selected = [row for row in rows if row["system"] == system]
        print(
            f"{system}: {sum(row['passed'] for row in selected)}/{len(selected)} passed (mock only)"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
