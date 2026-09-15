#!/bin/zsh
# PPR を専用venvで起動する。GPT-SoVITS環境へはフォールバックしない。
set -eu
SCRIPT_DIR="${0:A:h}"
cd "$SCRIPT_DIR"

VENV_PYTHON="$SCRIPT_DIR/.venv/bin/python"
if [[ ! -x "$VENV_PYTHON" ]]; then
  print -u2 "PPR専用venvが見つかりません: $VENV_PYTHON"
  print -u2 ""
  print -u2 "次の手順でセットアップしてください。"
  print -u2 "  /opt/homebrew/bin/python3.14 -m venv .venv"
  print -u2 "  .venv/bin/python -m pip install -e '.[dev]'"
  print -u2 ""
  print -u2 "Enterキーを押すと終了します。"
  read -r
  exit 1
fi

exec "$VENV_PYTHON" -m ppr "$@"
