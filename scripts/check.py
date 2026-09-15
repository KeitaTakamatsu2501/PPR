#!/usr/bin/env python3
"""lint / compile / unittest をまとめて実行する。いずれか失敗で非zero終了。

実LLMリクエストと本番への反映は含めない。
JTSが参照できる場合は実データ検証も走る（PPR_TEST_JTS_ROOT で場所を指定できる）。
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"


def _env() -> dict[str, str]:
    env = dict(os.environ)
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = f"{SRC}{os.pathsep}{existing}" if existing else str(SRC)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def run(label: str, command: list[str]) -> bool:
    print(f"\n=== {label} ===")
    print("$ " + " ".join(command))
    result = subprocess.run(command, cwd=ROOT, env=_env())
    ok = result.returncode == 0
    print(("OK: " if ok else "失敗: ") + label)
    return ok


def main() -> int:
    python = sys.executable
    ok = True
    ok &= run("compile", [python, "-m", "compileall", "-q", str(SRC), str(ROOT / "tests")])

    if importlib.util.find_spec("ruff") is None:
        print("\n=== ruff ===\n(スキップ: ruff が未インストール。`pip install -e '.[dev]'` で入ります)")
    else:
        ok &= run("ruff", [python, "-m", "ruff", "check", "src", "tests", "scripts"])

    ok &= run("unittest", [python, "-m", "unittest", "discover", "-s", "tests", "-t", str(ROOT), "-v"])

    if not ok:
        print("\n検証に失敗しました。")
        return 1
    print("\nすべて合格しました。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
