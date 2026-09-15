"""作品ごとの構成作家との接続。registryはコードで登録する。外部pluginは読まない。"""

from __future__ import annotations

from .jts_dialogue import JtsDialogueScheme
from .sbt_thread import SbtThreadScheme

SCHEMES = {
    JtsDialogueScheme.scheme_id: JtsDialogueScheme,
    SbtThreadScheme.scheme_id: SbtThreadScheme,
}


def create_scheme(scheme_id: str):
    factory = SCHEMES.get(scheme_id)
    if factory is None:
        raise KeyError(f"未登録のschemeです: {scheme_id}（登録済み: {sorted(SCHEMES)}）")
    return factory()


__all__ = ["SCHEMES", "JtsDialogueScheme", "SbtThreadScheme", "create_scheme"]
