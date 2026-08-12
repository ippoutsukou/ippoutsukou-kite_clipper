from __future__ import annotations

import sys
from pathlib import Path


FRAME_SCORER_ARGV_FLAG = "--__run-frame-scorer"


def build_predict_command(
    root: Path, sport: str | None = None, extra: list[str] | None = None
) -> list[str]:
    """`frame_scorer.py predict`（凍結時は内蔵サブコマンド経由）を起動するコマンド列を組み立てます。

    `sport` を渡すと `--sport <sport>` を先頭の追加引数として付与します（マルチスポーツ対応。
    仕様書 2.5 / TASKS.md 3-3）。`extra` には `--allow-profile-switch` など、`--sport` 以外の
    追加引数を渡してください。凍結時・非凍結時のどちらの分岐でも同じ extra_args を使うため、
    分岐ごとに書き漏らす心配はありません。
    """
    extra_args = list(extra or [])
    if sport:
        extra_args = ["--sport", sport, *extra_args]

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
