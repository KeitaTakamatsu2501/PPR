"""library.json の読み書き。

- 書き込みは 一時ファイル → flush/fsync → os.replace の atomic 置換。
- 置換の直前に、直前版を `library.prev.json` として保持する。途中で失敗しても
  直前の保存内容が壊れない。
- 一プロセスで編集する。二つ目のプロセスはロックを取得できず、閲覧専用になるか
  明示エラーになる。
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from .config import AppConfig
from .domain import Library, PersonaError
from .revisions import canonical_json


class LibraryLockedError(RuntimeError):
    """他のプロセスが編集中。"""


class LibraryStore:
    def __init__(self, config: AppConfig) -> None:
        self.config = config

    # ---- 読み書き -----------------------------------------------------

    def load(self) -> Library:
        path = self.config.library_path
        if not path.exists():
            return Library()
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise PersonaError(
                f"library.json を読み込めません: {exc}。"
                f"直前版が {self._previous_path()} にあれば、内容を確認してから手動で戻してください。"
            ) from exc
        return Library.from_json(raw)

    def save(self, library: Library) -> Path:
        path = self.config.library_path
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = canonical_json(library.to_json())

        if path.exists():
            previous = self._previous_path()
            previous.write_bytes(path.read_bytes())

        tmp = path.with_name(path.name + ".tmp")
        with tmp.open("w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
        self._fsync_directory(path.parent)
        return path

    def _previous_path(self) -> Path:
        return self.config.library_path.with_name("library.prev.json")

    @staticmethod
    def _fsync_directory(directory: Path) -> None:
        try:
            fd = os.open(directory, os.O_RDONLY)
        except OSError:
            return
        try:
            os.fsync(fd)
        except OSError:
            pass
        finally:
            os.close(fd)


class LibraryLock:
    """PIDを書いた排他ロック。取得できなければ編集させない。"""

    def __init__(self, config: AppConfig) -> None:
        self.path = config.lock_path
        self._fd: int | None = None

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError as exc:
            holder = self._holder_pid()
            if holder is not None and self._process_alive(holder):
                raise LibraryLockedError(
                    f"別のPPRがこのデータを編集中です（PID {holder}）。"
                    "同時編集はできません。"
                ) from exc
            # 保持者が生きていない残骸。取り除いて取り直す。
            try:
                self.path.unlink()
            except OSError:
                pass
            fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, str(os.getpid()).encode("ascii"))
        os.fsync(fd)
        self._fd = fd

    def release(self) -> None:
        if self._fd is not None:
            try:
                os.close(self._fd)
            except OSError:
                pass
            self._fd = None
        try:
            self.path.unlink()
        except OSError:
            pass

    def _holder_pid(self) -> int | None:
        try:
            return int(self.path.read_text(encoding="ascii").strip())
        except (OSError, ValueError):
            return None

    @staticmethod
    def _process_alive(pid: int) -> bool:
        if pid <= 0:
            return False
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        except OSError:
            return True
        return True

    def __enter__(self) -> LibraryLock:
        self.acquire()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.release()
