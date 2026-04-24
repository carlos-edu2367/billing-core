from __future__ import annotations

import os
import signal
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent

SERVICE_COMMANDS = {
    "api": [sys.executable, "-m", "app.web.main"],
    "worker": [sys.executable, "-m", "app.workers.worker"],
    "migrate": [sys.executable, "-m", "alembic", "upgrade", "head"],
}


def _run_command(command: list[str]) -> int:
    process = subprocess.Popen(
        command,
        cwd=ROOT,
        env=os.environ.copy(),
    )

    def _handle_signal(signum, _frame):
        if process.poll() is None:
            process.send_signal(signum)

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)
    return process.wait()


def main() -> int:
    mode = os.getenv("SERVICE_MODE", "api").strip().lower()
    if len(sys.argv) > 1:
        mode = sys.argv[1].strip().lower()

    if mode not in SERVICE_COMMANDS:
        valid_modes = ", ".join(sorted(SERVICE_COMMANDS))
        sys.stderr.write(f"Modo de servico invalido: {mode}. Use um de: {valid_modes}.\n")
        return 2

    if mode != "migrate" and os.getenv("RUN_MIGRATIONS_ON_START", "false").lower() == "true":
        migration_result = _run_command(SERVICE_COMMANDS["migrate"])
        if migration_result != 0:
            return migration_result

    return _run_command(SERVICE_COMMANDS[mode])


if __name__ == "__main__":
    raise SystemExit(main())
