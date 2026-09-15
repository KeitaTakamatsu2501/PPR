"""呼称契約 — 話者が相手をどう指すか、人類との境界をどう言い分けるか。

Phase 0 の実測（docs/PHASE_0_FINDINGS.md §5）で確認したとおり、これは
「相手を何と呼ぶか」だけの話ではない。二人称の単複と人類境界の契約であり、
作品に依存しない。SBTでも同じものが要る。

導出に使うのは人格が持つ3つの値だけ。

- `humanity_membership`（話者自身と相手のプロフィール上の人類所属）
- `direct_address_override`（この相手に限った呼称の例外）
- `perceived_humanity_override`（この話者から見た相手の人類所属）

契約は呼称に必要な限定情報に絞る。相手の人格・来歴・価値観は渡さない。
"""

from __future__ import annotations

from dataclasses import dataclass

from ..domain import PersonaDocument, PersonStance

DEFAULT_SECOND_PERSON = "あなた"
DEFAULT_FIRST_PERSON = "私"

#: 呼称は1行に収める。改行を含む値は前段で弾く。
MAX_ADDRESS_CHARACTERS = 80

INHERIT_VALUES = ("", "inherit")

SOURCE_RELATION = "relation"
SOURCE_PROFILE = "profile"
SOURCE_DEFAULT = "default"
SOURCE_FALLBACK = "fallback"

CONTRACT_HEADING = "【相手との呼称・人類所属契約】"
CONTRACT_CLOSING = (
    "この契約は呼称に必要な限定情報です。"
    "ここにない相手の人格・来歴・価値観を推測しないでください。"
)


def single_line(value: str, *, maximum: int = MAX_ADDRESS_CHARACTERS) -> str:
    """契約へ載せるために1行へ均す。人格の保存値は変更しない。"""
    first = value.replace("\r\n", "\n").split("\n", 1)[0].strip()
    return first[:maximum]


@dataclass(frozen=True)
class AddressingContract:
    speaker_persona_id: str
    speaker_name: str
    first_person: str
    speaker_humanity: str
    partner_persona_id: str
    partner_name: str
    partner_species_label: str
    partner_profile_humanity: str
    direct_address: str
    direct_address_source: str
    perceived_partner_humanity: str
    perceived_source: str

    @property
    def is_meaningful(self) -> bool:
        """呼称か人類所属のどちらかに、既定以外の情報があるか。"""
        return bool(
            self.direct_address_source == SOURCE_RELATION
            or self.perceived_source == SOURCE_RELATION
            or self.speaker_humanity
            or self.perceived_partner_humanity
        )

    def _self_membership_rule(self) -> str:
        first = self.first_person or DEFAULT_FIRST_PERSON
        if self.speaker_humanity == "excluded":
            return (
                f"自分と人類の両方へ述べる場合は「{first}や人間たち」など、"
                "自分を人類から分けた表現にしてください。"
            )
        if self.speaker_humanity == "included":
            return (
                f"自分と人類の両方へ述べる場合は「{first}たち人間」など、"
                "自分を人類に含む表現を使えます。"
            )
        return "自分が人類に含まれるかも断定せず、包含・分離を表す集合呼称を避けてください。"

    def _partner_membership_rule(self) -> str:
        if self.perceived_partner_humanity == "included":
            return (
                "この相手は人類に含まれます。人類全体への評価と、"
                "会話相手個人への評価を取り違えないよう、どちらを指しているか明示してください。"
            )
        if self.perceived_partner_humanity == "excluded":
            return (
                "この相手は人類に含まれません。人類への評価が相手個人への評価として"
                "伝わらないよう、対象を分けて述べてください。"
            )
        return (
            "この相手が人類に含まれるかは断定されていません。"
            "含む・含まないのどちらかに決めつけた集合呼称を使わないでください。"
        )

    def render(self) -> str:
        address = self.direct_address
        name = self.partner_name
        lines = [
            CONTRACT_HEADING,
            f"会話相手: {name}",
        ]
        if self.partner_species_label:
            lines.append(f"相手の種族・存在種別: {self.partner_species_label}")
        lines.extend(
            [
                f"相手を直接指す二人称: {address}",
                f"自分を指す一人称: {self.first_person or DEFAULT_FIRST_PERSON}",
                f"自分の人類所属: {self.speaker_humanity or 'unspecified'}",
                f"相手のプロフィール上の人類所属: {self.partner_profile_humanity or 'unspecified'}",
                f"この話者から見た相手の人類所属: {self.perceived_partner_humanity or 'unspecified'}",
                "単独の相手を二人称で指すときは、上記の呼称だけを使ってください。"
                "相手の固有名を自然に呼ぶことはできます。"
                "別の二人称と同じ文字を含む作品名・引用を述べる場合は、必ず括弧で引用だと明示してください。",
                f"会話相手だけへ述べる場合は「{address}」または固有名「{name}」で、"
                "単独の相手だと明示してください。",
                "人類だけへ述べる場合は「人間たち」または「人類」と明示し、"
                "会話相手を含む二人称に置き換えないでください。",
                f"相手と人類の両方へ述べる場合は「{address}や人間たち」または「{name}と人類」など、"
                "両者を明示して分けてください。",
                "「あなた方」「君たち」などの裸の複数二人称は、"
                "相手だけ・人類だけ・両方の境界を曖昧にするため使わないでください。",
                self._partner_membership_rule(),
                self._self_membership_rule(),
                CONTRACT_CLOSING,
            ]
        )
        return "\n".join(lines)

    def summary(self) -> dict[str, str]:
        """runへ記録するための要約。どの値がどこ由来かを残す。"""
        return {
            "speaker_persona_id": self.speaker_persona_id,
            "partner_persona_id": self.partner_persona_id,
            "direct_address": self.direct_address,
            "direct_address_source": self.direct_address_source,
            "speaker_humanity": self.speaker_humanity,
            "partner_profile_humanity": self.partner_profile_humanity,
            "perceived_partner_humanity": self.perceived_partner_humanity,
            "perceived_source": self.perceived_source,
        }


def resolve_direct_address(
    speaker: PersonaDocument, relation: PersonStance | None
) -> tuple[str, str]:
    """この相手に使う二人称と、その出どころを返す。"""
    if relation is not None:
        override = single_line(relation.direct_address_override)
        if override:
            return override, SOURCE_RELATION
    default = single_line(speaker.second_person)
    if default:
        return default, SOURCE_DEFAULT
    return DEFAULT_SECOND_PERSON, SOURCE_FALLBACK


def resolve_perceived_humanity(
    partner: PersonaDocument, relation: PersonStance | None
) -> tuple[str, str]:
    """この話者から見た相手の人類所属と、その出どころを返す。"""
    if relation is not None:
        override = relation.perceived_humanity_override.strip()
        if override not in INHERIT_VALUES:
            return override, SOURCE_RELATION
    return partner.humanity_membership, SOURCE_PROFILE


def resolve_contract(
    speaker: PersonaDocument, partner: PersonaDocument
) -> AddressingContract:
    """保存済みの人格2つから、ペア固有の呼称契約を導く。"""
    relation = speaker.person_stance_for(partner.persona_id)
    address, address_source = resolve_direct_address(speaker, relation)
    perceived, perceived_source = resolve_perceived_humanity(partner, relation)
    return AddressingContract(
        speaker_persona_id=speaker.persona_id,
        speaker_name=speaker.name,
        first_person=single_line(speaker.first_person),
        speaker_humanity=speaker.humanity_membership,
        partner_persona_id=partner.persona_id,
        partner_name=single_line(partner.name, maximum=120),
        partner_species_label=single_line(partner.species_label, maximum=120),
        partner_profile_humanity=partner.humanity_membership,
        direct_address=address,
        direct_address_source=address_source,
        perceived_partner_humanity=perceived,
        perceived_source=perceived_source,
    )
