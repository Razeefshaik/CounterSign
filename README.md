# CounterSign

An AI incident commander for a small marketplace's disputes and weekly maker
payouts. The AI investigates and proposes; deterministic code checks safety and
executes approved actions.

This project is under development for the PayPal AI Hackathon. The repository
currently contains the project skeleton and a local mock PayPal server. The
CounterSign application and sandbox verification harness are not implemented yet.

## Development setup

Use Python 3.12. From the repository root on Windows:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
```

If PowerShell does not allow activation, use `.\.venv\Scripts\python.exe` in
place of `python`, and run the check executables from `.venv\Scripts\`.

Run the required checks:

```powershell
python -m pytest
ruff check
lint-imports
```

Tests use fake data and do not need PayPal credentials. For the standalone mock
server, see [mock_paypal/README.md](mock_paypal/README.md).

## Project rules and design

Read [AGENTS.md](AGENTS.md) before making changes. The design is in
[docs/HANDOFF.md](docs/HANDOFF.md); interfaces and task boundaries are in
[docs/CONTRACTS.md](docs/CONTRACTS.md) and [docs/TASKS.md](docs/TASKS.md).

PayPal integrations are sandbox only. Keep credentials in an untracked local
`.env`, using the variable names in `.env.example`. Never commit credentials or
raw recordings. Import contracts prevent the agent from reaching money-write
code and restrict the write adapter to the executor.

Licensed under [Apache 2.0](LICENSE).
