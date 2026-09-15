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

#: 予算が十分なときは原典を全文渡す。断片抽出はあくまで収まらないときの手段。
#: 実データで最も大きい人格（久本礼司）の全文描画が約19,600字なので、
#: 既定はそれを確実に上回る値にしている。Bedrockのように広い文脈が使える場合、
#: 人格を削る理由がない。
DEFAULT_MAX_CHARACTERS = 24000

#: 32kコンテキストで2人を通すときの値。Phase 0 でこの値での再現を確認している。
COMPACT_MAX_CHARACTERS = 4000

#: 日本語の保守的なトークン見積もり（JTS由来）。実測ではこれより小さく収まる。
TOKENS_PER_CHARACTER = 1.8

MODE_FULL = "full"
MODE_CAPSULE = "capsule"

#: この文字数を超える行だけ文単位へ割る。短い行は列挙や設定値なので割らない。
LINE_SPLIT_THRESHOLD = 160

CAPSULE_TITLE = "【人格カプセル】"
CAPSULE_NOTE = "以下は保存済み原典の完全な行または文だけを選んだものである。未収録箇所を否定する資料ではない。"

FULL_TITLE = "【人格原典】"
FULL_NOTE = "以下は保存済み原典の全文である。省略も要約もしていない。"

FULL_SECTION_LABELS: tuple[tuple[str, str], ...] = (
    ("overview", "キャラクター概要"),
    ("basic_settings", "基本設定"),
    ("speaking_style", "口調・話し方"),
    ("dialogue_samples", "セリフのサンプル"),
)

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

#: 優先度ごとの取り分（常時採用分を除いた予算に対する割合）。
#: 単純な優先順だけで詰めると、区画数の多い口調が予算を食い尽くし、
#: 人格の核が1つも入らないことがある。まず各群に取り分を与え、
#: 余った分を優先順で配り直す。
PRIORITY_SHARES: dict[int, float] = {
    P_OVERVIEW: 0.22,
    P_IDENTITY: 0.30,
    P_VOICE: 0.24,
    P_PROHIBITION: 0.08,
    P_SAMPLE: 0.14,
    P_OTHER: 0.02,
}

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
    mode: str = MODE_CAPSULE

    @property
    def truncated(self) -> bool:
        return bool(self.omitted)

    @property
    def is_full(self) -> bool:
        return self.mode == MODE_FULL

    @property
    def characters(self) -> int:
        return len(self.text)

    def summary(self) -> dict[str, object]:
        """runへ記録するための要約。何を落としたかを後から読める。"""
        return {
            "mode": self.mode,
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
    used = len(header) + sum(len(f.render()) + 1 for f in selected)
    remaining_budget = max(0, max_characters - used)

    candidates = sorted(
        (f for f in fragments if not f.always), key=lambda f: (f.priority, f.order)
    )

    # 第1巡：群ごとの取り分の範囲で詰める。
    spent: dict[int, int] = {}
    deferred: list[Fragment] = []
    for fragment in candidates:
        cost = len(fragment.render()) + 1
        share = int(remaining_budget * PRIORITY_SHARES.get(fragment.priority, 0.0))
        if spent.get(fragment.priority, 0) + cost > share or used + cost > max_characters:
            deferred.append(fragment)
            continue
        selected.append(fragment)
        spent[fragment.priority] = spent.get(fragment.priority, 0) + cost
        used += cost

    # 第2巡：余った予算を優先順で配り直す。
    omitted: list[Fragment] = []
    for fragment in deferred:
        cost = len(fragment.render()) + 1
        if used + cost > max_characters:
            omitted.append(fragment)
            continue
        selected.append(fragment)
        used += cost

    selected.sort(key=lambda f: f.order)
    lines = [header, *(f.render() for f in selected)]
    return PersonaCapsule(
        text="\n".join(lines),
        included=tuple(selected),
        omitted=tuple(sorted(omitted, key=lambda f: f.order)),
        source_characters=source_characters,
        max_characters=max_characters,
    )


def render_full_persona(document: PersonaDocument, *, partner_character_id: str = "") -> str:
    """原典を全文そのまま渡す描画。見出し構造を保つ。

    予算が足りるならこちらを使う。断片抽出は情報を落とすので、落とす必要が
    ないときに使う理由がない。
    """
    lines = [FULL_TITLE, FULL_NOTE, "", f"【{FIXED_PROFILE_HEADING}】"]
    for label, value in (
        ("名前", document.name),
        ("一人称", document.first_person),
        ("二人称", document.second_person),
        ("種族・存在種別", document.species_label),
    ):
        lines.append(f"{label}: {value}")
    if document.humanity_membership:
        lines.append(f"人類との関係: {document.humanity_membership}")

    for field_name, label in FULL_SECTION_LABELS:
        body = getattr(document, field_name)
        if not body.strip():
            continue
        lines.extend(["", f"【{label}】", body])

    relation = document.person_stance_for(partner_character_id)
    if relation is not None and relation.stance.strip():
        lines.extend(["", f"【{RELATION_HEADING}】", f"- {relation.name}: {relation.stance}"])
    return "\n".join(lines)


def build_persona_rendering(
    document: PersonaDocument,
    *,
    partner_character_id: str = "",
    max_characters: int = DEFAULT_MAX_CHARACTERS,
) -> PersonaCapsule:
    """予算に収まるなら全文、収まらないなら断片抽出。

    どちらを使ったかは `mode` に残る。作者が「今回は削られたのか」を判別できる。
    """
    full_text = render_full_persona(document, partner_character_id=partner_character_id)
    if len(full_text) <= max_characters:
        return PersonaCapsule(
            text=full_text,
            included=collect_fragments(document, partner_character_id=partner_character_id),
            omitted=(),
            source_characters=document.body_characters,
            max_characters=max_characters,
            mode=MODE_FULL,
        )
    return build_capsule(
        document,
        partner_character_id=partner_character_id,
        max_characters=max_characters,
    )


def persona_budget_for(
    context_limit_tokens: int,
    *,
    speakers: int = 2,
    share: float = 0.45,
    tokens_per_character: float = TOKENS_PER_CHARACTER,
) -> int:
    """文脈の上限から、1話者に割ける文字数を出す。

    `share` は人格へ回す割合。残りは構成作家の指示、素材、これまでの発話に使う。
    見積もりは保守的な値を使い、足りない側へ倒す。
    """
    if context_limit_tokens <= 0 or speakers <= 0:
        return COMPACT_MAX_CHARACTERS
    tokens = context_limit_tokens * share / speakers
    return max(COMPACT_MAX_CHARACTERS, int(tokens / tokens_per_character))
