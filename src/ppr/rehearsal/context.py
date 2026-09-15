"""人格コンテキスト — 保存済みの人格を、作品へ渡せる形へ組み立てる。

層1（人格）と層2（構成作家）の境界。ここまでが作品非依存で、ここから先が作品固有。

Phase 0 の実測（docs/PHASE_0_FINDINGS.md §4）で確認した3経路を統合する。

1. 人格カプセル — 原文の断片で上限内に収める
2. 恒常的な価値境界 — 原文を全文そのまま
3. 話題に関わる小カテゴリ — 選ばれた分の原文だけ

小カテゴリの選択はモデルに任せる方針だが（docs/PHASE_PLAN.md §7.2）、この関数は
選択結果を引数で受け取る純粋関数とする。組み立ての正しさと選択の巧拙を切り分けて
検証できるようにするため。選択そのものは runner が行う。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..domain import DEFAULT_APPLIED_MODES, DIALOGUE_MODES, PersonaDocument, TopicStance
from ..revisions import revision_id_for
from .addressing import AddressingContract, resolve_contract
from .capsule import DEFAULT_MAX_CHARACTERS, PersonaCapsule, build_persona_rendering

#: 1話者あたりに渡す小カテゴリの上限。Phase 0 の実測値に合わせる。
MAX_SELECTED_CATEGORIES = 4

APPLICATION_BOUNDARY = (
    "【設定の適用境界】\n"
    "人物関係に明記された相手についてのみ、その関係設定を反映してください。"
    "明記されていない関係を推測しないでください。\n"
    "相手の非公開の設定文や内面を、既知のものとして話さないでください。\n"
    "共有した過去や関係性を新しく作らないでください。"
)


@dataclass(frozen=True)
class PersonaContext:
    """1人分の、作品へ渡せる人格。"""

    persona_id: str
    locale: str
    revision_id: str
    name: str
    actor_ref: str
    text: str
    capsule: PersonaCapsule
    contract: AddressingContract
    value_boundary_text: str
    selected_categories: tuple[TopicStance, ...]
    unknown_category_names: tuple[str, ...]
    applied_modes: tuple[str, ...]
    available_category_names: tuple[str, ...] = field(default_factory=tuple)

    @property
    def characters(self) -> int:
        return len(self.text)

    def summary(self) -> dict[str, Any]:
        """runへ記録する。実際に何を送ったかが後から読める。"""
        return {
            "persona_id": self.persona_id,
            "locale": self.locale,
            "revision_id": self.revision_id,
            "actor_ref": self.actor_ref,
            "characters": self.characters,
            "capsule": self.capsule.summary(),
            "addressing": self.contract.summary(),
            "value_boundary_characters": len(self.value_boundary_text),
            "selected_categories": [t.category for t in self.selected_categories],
            "unknown_category_names": list(self.unknown_category_names),
            "applied_modes": list(self.applied_modes),
            "available_category_count": len(self.available_category_names),
        }


def topic_category_candidates(document: PersonaDocument) -> tuple[str, ...]:
    """モデルへ提示する候補。小カテゴリの名前だけを、本文なしで渡す。

    大分類は候補にしない。恒常的な価値境界は別経路で全文が入るため除く。
    """
    seen: list[str] = []
    for stance in document.small_category_stances:
        if stance.category not in seen:
            seen.append(stance.category)
    return tuple(seen)


def resolve_selected_categories(
    document: PersonaDocument,
    names: tuple[str, ...] | list[str],
    *,
    maximum: int = MAX_SELECTED_CATEGORIES,
) -> tuple[tuple[TopicStance, ...], tuple[str, ...]]:
    """モデルが返した名前を実際の行へ対応づける。

    知らない名前は推測で埋めない。未知の名前はそのまま返し、呼び出し側が記録する。
    """
    by_category: dict[str, TopicStance] = {}
    for stance in document.small_category_stances:
        by_category.setdefault(stance.category, stance)

    resolved: list[TopicStance] = []
    unknown: list[str] = []
    used: set[str] = set()
    for raw in names:
        name = str(raw).strip()
        if not name or name in used:
            continue
        stance = by_category.get(name)
        if stance is None:
            unknown.append(name)
            continue
        used.add(name)
        resolved.append(stance)
        if len(resolved) >= maximum:
            break
    return tuple(resolved), tuple(unknown)


def _render_value_boundary(document: PersonaDocument, applied_modes: tuple[str, ...]) -> str:
    boundary = document.value_boundary
    if boundary is None or not boundary.stance.strip():
        return ""
    excluded = tuple(m for m in DIALOGUE_MODES if m not in applied_modes)
    lines = [
        "【恒常的な価値境界】",
        boundary.stance,
        "",
        "【恒常的な価値境界の適用範囲】",
        "上記は人格の原典であり、原文のまま渡しています。",
    ]
    if applied_modes:
        lines.append(
            "本文に含まれるモード別の指示のうち、今回適用するのは "
            f"{'と'.join(applied_modes)} に関する指示だけです。"
        )
    if excluded:
        lines.append(f"{'・'.join(excluded)} に関する指示は適用しません。")
    lines.append(
        "保護対象、忌避価値、反射的反論、同意境界は、モードに関わらず維持してください。"
    )
    lines.append(
        "出力形式や場面の指定は作品側が定めますが、人格の価値判断を上書きしません。"
    )
    return "\n".join(lines)


def _render_selected_categories(selected: tuple[TopicStance, ...]) -> str:
    if not selected:
        return ""
    lines = ["【この話題に関わる価値観】", "原文のまま渡しています。要約ではありません。"]
    lines.extend(f"- {stance.category}: {stance.stance}" for stance in selected)
    return "\n".join(lines)


def build_persona_context(
    document: PersonaDocument,
    partner: PersonaDocument,
    *,
    actor_ref: str = "a",
    selected_category_names: tuple[str, ...] | list[str] = (),
    max_persona_characters: int = DEFAULT_MAX_CHARACTERS,
    applied_modes: tuple[str, ...] = DEFAULT_APPLIED_MODES,
    maximum_categories: int = MAX_SELECTED_CATEGORIES,
) -> PersonaContext:
    """保存済みの人格から、作品へ渡す人格コンテキストを組み立てる。

    純粋関数。同じ入力からは必ず同じ文字列を返す。モデルへは問い合わせない。
    """
    capsule = build_persona_rendering(
        document,
        partner_character_id=partner.persona_id,
        max_characters=max_persona_characters,
    )
    contract = resolve_contract(document, partner)
    selected, unknown = resolve_selected_categories(
        document, selected_category_names, maximum=maximum_categories
    )
    boundary_text = _render_value_boundary(document, applied_modes)
    categories_text = _render_selected_categories(selected)

    blocks = [
        f"【あなたが演じる人物: {document.name}】",
        capsule.text,
        contract.render(),
    ]
    if boundary_text:
        blocks.append(boundary_text)
    if categories_text:
        blocks.append(categories_text)
    blocks.append(APPLICATION_BOUNDARY)

    return PersonaContext(
        persona_id=document.persona_id,
        locale=document.locale,
        revision_id=revision_id_for(document),
        name=document.name,
        actor_ref=actor_ref,
        text="\n\n".join(blocks),
        capsule=capsule,
        contract=contract,
        value_boundary_text=boundary_text,
        selected_categories=selected,
        unknown_category_names=unknown,
        applied_modes=tuple(applied_modes),
        available_category_names=topic_category_candidates(document),
    )
