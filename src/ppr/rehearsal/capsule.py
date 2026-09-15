"""人格カプセル — 原文の断片だけで人格を上限内に収める。

Phase 0 の実測（docs/PHASE_0_FINDINGS.md §4）で確認した方針を踏襲する。

- 縮約は常に原文の行または文の抽出で行う。LLMによる要約は使わない。
- 各断片に出典の見出しを付ける。どこから来た文かを作者が追える。
- 収まらなかった断片は数を記録する。黙って切り捨てたことにしない。
- 保存されている人格は一切変更しない。カプセルは渡すための一時的な見え方である。

優先順位は「誰か」と「どう話すか」を厚く、「何を考えるか」は薄くする。
価値判断の中身はカプセルではなく、恒常的な価値境界の全文と、話題に応じて
選ばれた小カテゴリの原文で渡るため（docs/PHASE_PLAN.md §7.2）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..domain import PersonaDocument
from ..sections import split_sections

DEFAULT_MAX_CHARACTERS = 4000

#: この文字数を超える行だけ文単位へ割る。短い行は列挙や設定値なので割らない。
LINE_SPLIT_THRESHOLD = 160

CAPSULE_TITLE = "【人格カプセル】"
CAPSULE_NOTE = "以下は保存済み原典の完全な行または文だけを選んだものである。未収録箇所を否定する資料ではない。"

FIXED_PROFILE_HEADING = "固定プロフィール"
RELATION_HEADING = "人物ごとの感情・関係性"

# 優先度。小さいほど先に入る。
P_FIXED = 0        # 固定プロフィールと相手への関係。常に入れる
P_OVERVIEW = 1     # キャラクター概要
P_IDENTITY = 2     # 基本属性・人格の核など、何者かを述べる区画
P_VOICE = 3        # 声と発話構造
P_PROHIBITION = 4  # 避ける話し方・禁止・崩壊防止
P_SAMPLE = 5       # セリフのサンプル
P_OTHER = 6        # それ以外

IDENTITY_KEYWORDS = (
    "基本プロフィール", "基本属性", "基本情報", "人格の核", "概要", "現在の立場", "基本設定",
)
VOICE_KEYWORDS = (
    "声と基調", "基本の声", "口調", "発話構造", "発話リズム", "発話順序", "語尾", "話法", "語彙",
)
PROHIBITION_KEYWORDS = (
    "避ける", "禁止", "崩壊防止", "境界", "しない",
)


@dataclass(frozen=True)
class Fragment:
    """原文から取り出した1断片。前後の空白のみ除いた、それ以外は原文のまま。"""

    heading: str
    text: str
    priority: int
    order: int
    always: bool = False

    def render(self) -> str:
        label = f"出典【{self.heading}】" if self.heading else "出典"
        return f"- {label}: {self.text}"


@dataclass(frozen=True)
class PersonaCapsule:
    text: str
    included: tuple[Fragment, ...]
    omitted: tuple[Fragment, ...]
    source_characters: int
    max_characters: int

    @property
    def truncated(self) -> bool:
        return bool(self.omitted)

    @property
    def characters(self) -> int:
        return len(self.text)

    def summary(self) -> dict[str, object]:
        """runへ記録するための要約。何を落としたかを後から読める。"""
        return {
            "source_characters": self.source_characters,
            "capsule_characters": self.characters,
            "max_characters": self.max_characters,
            "included_fragments": len(self.included),
            "omitted_fragments": len(self.omitted),
            "omitted_headings": sorted({f.heading for f in self.omitted}),
        }


def _classify(heading: str, *, is_sample: bool) -> int:
    if is_sample:
        return P_SAMPLE
    for keyword in PROHIBITION_KEYWORDS:
        if keyword in heading:
            return P_PROHIBITION
    for keyword in VOICE_KEYWORDS:
        if keyword in heading:
            return P_VOICE
    for keyword in IDENTITY_KEYWORDS:
        if keyword in heading:
            return P_IDENTITY
    return P_OTHER


def split_lines_and_sentences(text: str) -> list[str]:
    """行に割り、長い行だけ文へ割る。内容は変えない。"""
    pieces: list[str] = []
    for raw_line in text.split("\n"):
        line = raw_line.strip()
        if not line:
            continue
        if len(line) <= LINE_SPLIT_THRESHOLD:
            pieces.append(line)
            continue
        parts = re.split(r"(?<=[。！？])", line)
        merged: list[str] = []
        for part in parts:
            if not part:
                continue
            if merged and part[0] in "」』）)":
                merged[-1] += part
            else:
                merged.append(part)
        pieces.extend(p.strip() for p in merged if p.strip())
    return pieces


def collect_fragments(
    document: PersonaDocument, *, partner_character_id: str = ""
) -> tuple[Fragment, ...]:
    """人格から断片を集める。原文中の出現順を `order` に保つ。"""
    fragments: list[Fragment] = []
    order = 0

    def add(heading: str, text: str, priority: int, *, always: bool = False) -> None:
        nonlocal order
        if not text.strip():
            return
        fragments.append(
            Fragment(heading=heading, text=text.strip(), priority=priority, order=order, always=always)
        )
        order += 1

    for label, value in (
        ("名前", document.name),
        ("一人称", document.first_person),
        ("二人称", document.second_person),
        ("種族・存在種別", document.species_label),
    ):
        add(FIXED_PROFILE_HEADING, f"{label}: {value}", P_FIXED, always=True)
    if document.humanity_membership:
        add(FIXED_PROFILE_HEADING, f"人類との関係: {document.humanity_membership}", P_FIXED, always=True)

    for field, priority, is_sample in (
        ("overview", P_OVERVIEW, False),
        ("basic_settings", None, False),
        ("speaking_style", None, False),
        ("dialogue_samples", None, True),
    ):
        body = getattr(document, field)
        for section in split_sections(body):
            heading = section.heading or "（前書き）"
            level = priority if priority is not None else _classify(heading, is_sample=is_sample)
            for piece in split_lines_and_sentences(section.body if section.heading else section.text):
                add(heading, piece, level)

    relation = document.person_stance_for(partner_character_id)
    if relation is not None and relation.stance.strip():
        add(RELATION_HEADING, f"{relation.name}: {relation.stance}", P_FIXED, always=True)

    return tuple(fragments)


def build_capsule(
    document: PersonaDocument,
    *,
    partner_character_id: str = "",
    max_characters: int = DEFAULT_MAX_CHARACTERS,
) -> PersonaCapsule:
    """上限内に収まる断片だけを選ぶ。常時採用の断片は上限に関わらず入れる。"""
    fragments = collect_fragments(document, partner_character_id=partner_character_id)
    header = f"{CAPSULE_TITLE}\n{CAPSULE_NOTE}"
    source_characters = document.body_characters

    selected: list[Fragment] = [f for f in fragments if f.always]
    budget = len(header) + sum(len(f.render()) + 1 for f in selected)

    candidates = sorted(
        (f for f in fragments if not f.always), key=lambda f: (f.priority, f.order)
    )
    omitted: list[Fragment] = []
    for fragment in candidates:
        cost = len(fragment.render()) + 1
        if budget + cost > max_characters:
            omitted.append(fragment)
            continue
        selected.append(fragment)
        budget += cost

    selected.sort(key=lambda f: f.order)
    lines = [header, *(f.render() for f in selected)]
    return PersonaCapsule(
        text="\n".join(lines),
        included=tuple(selected),
        omitted=tuple(sorted(omitted, key=lambda f: f.order)),
        source_characters=source_characters,
        max_characters=max_characters,
    )
