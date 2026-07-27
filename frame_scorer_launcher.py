from __future__ import annotations

import sys
from pathlib import Path


FRAME_SCORER_ARGV_FLAG = "--__run-frame-scorer"


def build_predict_command(root: Path, extra: list[str] | None = None) -> list[str]:
    extra_args = extra or []
    if getattr(sys, "frozen", False):
        return [
            sys.executable,
            FRAME_SCORER_ARGV_FLAG,
            "predict",
            "--root",
            str(root),
            *extra_args,
        ]

    python = root / ".venv" / "Scripts" / "python.exe"
    if not python.exists():
        python = Path(sys.executable)
    return [
        str(python),
        str(root / "frame_scorer.py"),
        "predict",
        "--root",
        str(root),
        *extra_args,
    ]
