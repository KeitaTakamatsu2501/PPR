"""資産化の対象。docs/PHASE_PLAN.md §3 の決定に対応する。

ヒロアキ（kugimiya-e03d64755b）は意図的に人格を薄くしたJTS特化の設定のため対象外。
研究用に作られた残り20人も移植・有効化の予定がないため対象外。

`enabled` フラグでは選べない。ja-JP 以外のlocaleでは全件 false のため
（docs/PHASE_0_FINDINGS.md §2）。IDを明示的に列挙する。
"""

from __future__ import annotations

ASSET_PERSONAS: tuple[tuple[str, str], ...] = (
    ("hayamin-2548d9d189", "ミラ"),
    ("kikuko-de58d3efda", "ヴェルミリア"),
    ("eyja-9ca78ddb88", "宙星リリカ"),
    ("muelsyse-dec1162707", "ラティア"),
    ("h-tsubasa-db3d1d5f64", "久本礼司"),
    ("elmirea-ab93505eb7", "テルミナス"),
    ("luciela-06fbc1148f", "リュシエラ"),
    ("ione-47f2d24ffb", "イオネ"),
)

ASSET_PERSONA_IDS: tuple[str, ...] = tuple(pid for pid, _ in ASSET_PERSONAS)
ASSET_PERSONA_NAMES: dict[str, str] = dict(ASSET_PERSONAS)

#: 対象外だがJTS側では現役。8人の person_stances から参照される。
JTS_ONLY_PERSONA_IDS: tuple[str, ...] = ("kugimiya-e03d64755b",)
