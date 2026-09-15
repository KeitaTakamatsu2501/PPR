"""起動設定。通常起動でネットワーク・資格情報解決・JTS参照を行わない。"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_DATA_DIR = APP_ROOT / "data"

DATA_DIR_ENV = "PPR_DATA_DIR"


@dataclass(frozen=True)
class AppConfig:
    data_dir: Path
    offline: bool = False

    @property
    def library_path(self) -> Path:
        return self.data_dir / "library.json"

    @property
    def revisions_dir(self) -> Path:
        return self.data_dir / "revisions"

    @property
    def imports_dir(self) -> Path:
        return self.data_dir / "imports"

    @property
    def drafts_dir(self) -> Path:
        return self.data_dir / "drafts"

    @property
    def lock_path(self) -> Path:
        return self.data_dir / "library.lock"

    def ensure_directories(self) -> None:
        for path in (self.data_dir, self.revisions_dir, self.imports_dir, self.drafts_dir):
            path.mkdir(parents=True, exist_ok=True)


def resolve_config(data_dir: str | os.PathLike[str] | None = None, *, offline: bool = False) -> AppConfig:
    """優先順位: 明示引数 → 環境変数 → PPR直下の data/。"""
    if data_dir is not None:
        resolved = Path(data_dir).expanduser()
    else:
        env = os.environ.get(DATA_DIR_ENV, "").strip()
        resolved = Path(env).expanduser() if env else DEFAULT_DATA_DIR
    return AppConfig(data_dir=resolved.resolve(), offline=offline)
