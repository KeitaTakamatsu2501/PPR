"""PPRの人格データモデル。

設計上の約束：

1. 文字列は保存時に strip / Unicode正規化 / 改行置換をしない。作者が入力した文字列を
   そのまま保つ。入力検証で「空白だけか」を判定することはあっても、保存値は変えない。
2. 配列は順序を保つ。同名カテゴリがあっても行を勝手に統合しない。
3. 行の識別には `entry_id` を使う。名前やカテゴリ名から同一性を推測しない。
4. 未知の相手ID、削除済みの相手ID、本文が空で呼称例外だけの関係も保持する。
5. JTS固有の値（作品側の都合）はここに持ち込まない。

このモジュールは Tk、HTTP、JTS、音声のいずれにも依存しない。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field, replace
from typing import Any

SCHEMA_VERSION = 1

#: 194件のテンプレート外に置かれる固有カテゴリ。全対象人格が保持し、
#: 試演contextの常時参照対象になる（docs/PHASE_0_FINDINGS.md §4）。
VALUE_BOUNDARY_CATEGORY = "恒常的な価値境界"

#: 「恒常的な価値境界」本文に含まれるモード見出し。原文は改稿しないが、
#: 接続部（層3）が適用範囲を判断するために位置を知る必要がある。
VALUE_BOUNDARY_SECTIONS = (
    "恒常的な忌避価値",
    "保護対象",
    "反射的反論",
    "モード別扱い",
    "同意境界",
)

#: JTS由来のモード識別子。PPRの既定は議論と意見交換。相談は適用しない
#: （docs/PHASE_PLAN.md §7.1）。
DIALOGUE_MODES = ("議論", "意見交換", "解説", "相談")
DEFAULT_APPLIED_MODES = ("議論", "意見交換")

HUMANITY_MEMBERSHIPS = ("included", "conditional", "excluded", "unspecified", "")
PERCEIVED_HUMANITY_OVERRIDES = ("inherit", "included", "conditional", "excluded", "")

SUPPLEMENT_KINDS = ("style_text", "topic_lexicon")


class PersonaError(ValueError):
    """人格データの不整合。"""


def new_entry_id() -> str:
    return f"e-{uuid.uuid4().hex[:12]}"


def new_persona_id() -> str:
    return f"ppr-{uuid.uuid4().hex}"


def _text(value: Any, label: str) -> str:
    """文字列として受け取る。内容は一切変形しない。"""
    if value is None:
        return ""
    if not isinstance(value, str):
        raise PersonaError(f"{label} は文字列である必要があります: {type(value).__name__}")
    return value


@dataclass(frozen=True)
class TopicStance:
    """価値観の1行。`category` は `大分類/小カテゴリ` の階層表記を取りうる。"""

    entry_id: str
    category: str
    stance: str

    @property
    def is_value_boundary(self) -> bool:
        return self.category == VALUE_BOUNDARY_CATEGORY

    @property
    def large_category(self) -> str:
        return self.category.split("/", 1)[0]

    @property
    def is_small_category(self) -> bool:
        """接続部がモデルへ提示する候補は小カテゴリのみ（PHASE_0_FINDINGS §4）。"""
        return "/" in self.category

    def to_json(self) -> dict[str, Any]:
        return {"entry_id": self.entry_id, "category": self.category, "stance": self.stance}

    @classmethod
    def from_json(cls, raw: Any) -> TopicStance:
        if not isinstance(raw, dict):
            raise PersonaError("topic_stances の要素はobjectである必要があります")
        return cls(
            entry_id=_text(raw.get("entry_id"), "topic_stances.entry_id") or new_entry_id(),
            category=_text(raw.get("category"), "topic_stances.category"),
            stance=_text(raw.get("stance"), "topic_stances.stance"),
        )


@dataclass(frozen=True)
class PersonStance:
    """人物関係の1行。

    `target_character_id` が空の行は、名前だけで結ばれた旧来の関係。名前から相手IDを
    推測しない。対象がPPR内に存在しなくても行は保持する。
    """

    entry_id: str
    name: str
    stance: str
    target_character_id: str = ""
    direct_address_override: str = ""
    perceived_humanity_override: str = ""

    def to_json(self) -> dict[str, Any]:
        return {
            "entry_id": self.entry_id,
            "name": self.name,
            "stance": self.stance,
            "target_character_id": self.target_character_id,
            "direct_address_override": self.direct_address_override,
            "perceived_humanity_override": self.perceived_humanity_override,
        }

    @classmethod
    def from_json(cls, raw: Any) -> PersonStance:
        if not isinstance(raw, dict):
            raise PersonaError("person_stances の要素はobjectである必要があります")
        return cls(
            entry_id=_text(raw.get("entry_id"), "person_stances.entry_id") or new_entry_id(),
            name=_text(raw.get("name"), "person_stances.name"),
            stance=_text(raw.get("stance"), "person_stances.stance"),
            target_character_id=_text(raw.get("target_character_id"), "person_stances.target_character_id"),
            direct_address_override=_text(raw.get("direct_address_override"), "person_stances.direct_address_override"),
            perceived_humanity_override=_text(raw.get("perceived_humanity_override"), "person_stances.perceived_humanity_override"),
        )


@dataclass(frozen=True)
class Supplement:
    """人格に付随する補助資料。schemeがキャラIDで分岐しないための受け皿。"""

    supplement_id: str
    kind: str
    content: str = ""
    data: dict[str, Any] = field(default_factory=dict)
    source_ref: str = ""
    editable: bool = True

    def to_json(self) -> dict[str, Any]:
        return {
            "supplement_id": self.supplement_id,
            "kind": self.kind,
            "content": self.content,
            "data": dict(self.data),
            "source_ref": self.source_ref,
            "editable": self.editable,
        }

    @classmethod
    def from_json(cls, raw: Any) -> Supplement:
        if not isinstance(raw, dict):
            raise PersonaError("supplements の要素はobjectである必要があります")
        kind = _text(raw.get("kind"), "supplements.kind")
        if kind not in SUPPLEMENT_KINDS:
            raise PersonaError(f"未対応の supplement kind です: {kind!r}")
        data = raw.get("data") or {}
        if not isinstance(data, dict):
            raise PersonaError("supplements.data はobjectである必要があります")
        return cls(
            supplement_id=_text(raw.get("supplement_id"), "supplements.supplement_id") or new_entry_id(),
            kind=kind,
            content=_text(raw.get("content"), "supplements.content"),
            data=dict(data),
            source_ref=_text(raw.get("source_ref"), "supplements.source_ref"),
            editable=bool(raw.get("editable", True)),
        )


@dataclass(frozen=True)
class PersonaDocument:
    """1つのlocaleにおける人格の全体。

    フィールドは Phase 0 の実測（docs/PHASE_0_FINDINGS.md §3〜§5）に対応する。
    音声は含まない。PPRでの作成・保存に音声を要求しない。
    """

    persona_id: str
    locale: str
    name: str
    first_person: str = ""
    second_person: str = ""
    species_label: str = ""
    humanity_membership: str = ""
    overview: str = ""
    basic_settings: str = ""
    speaking_style: str = ""
    dialogue_samples: str = ""
    topic_template_id: str = ""
    topic_stances: tuple[TopicStance, ...] = ()
    person_stances: tuple[PersonStance, ...] = ()
    supplements: tuple[Supplement, ...] = ()
    extensions: dict[str, Any] = field(default_factory=dict)

    # ---- 参照系 -------------------------------------------------------

    @property
    def value_boundary(self) -> TopicStance | None:
        """「恒常的な価値境界」の行。接続部が常時参照する。"""
        return next((t for t in self.topic_stances if t.is_value_boundary), None)

    @property
    def small_category_stances(self) -> tuple[TopicStance, ...]:
        """モデル選択の候補になる小カテゴリ（docs/PHASE_PLAN.md §7.2）。"""
        return tuple(t for t in self.topic_stances if t.is_small_category and t.stance.strip())

    @property
    def large_categories(self) -> tuple[str, ...]:
        seen: list[str] = []
        for t in self.topic_stances:
            if t.is_value_boundary:
                continue
            large = t.large_category
            if large and large not in seen:
                seen.append(large)
        return tuple(seen)

    def person_stance_for(self, target_character_id: str) -> PersonStance | None:
        """今回の相手に対応する関係だけを返す。

        JTSの `person_stance_for_partner` と同じ方針。`target_character_id` が
        設定された行は、その相手にだけ属する。同名の別人へ引き当てない。
        """
        target = (target_character_id or "").strip()
        if not target:
            return None
        return next((p for p in self.person_stances if p.target_character_id.strip() == target), None)

    @property
    def body_characters(self) -> int:
        return sum(
            len(v)
            for v in (self.overview, self.basic_settings, self.speaking_style, self.dialogue_samples)
        )

    # ---- 検証 ---------------------------------------------------------

    def validation_issues(self, *, for_rehearsal: bool = False) -> tuple[str, ...]:
        """保存は妨げない。未完成の人格も保存できる。"""
        issues: list[str] = []
        if not self.persona_id.strip():
            issues.append("persona_id が空です。")
        if not self.locale.strip():
            issues.append("locale が空です。")
        if self.humanity_membership not in HUMANITY_MEMBERSHIPS:
            issues.append(f"humanity_membership が未知の値です: {self.humanity_membership!r}")
        for p in self.person_stances:
            if p.perceived_humanity_override not in PERCEIVED_HUMANITY_OVERRIDES:
                issues.append(
                    f"perceived_humanity_override が未知の値です: {p.perceived_humanity_override!r}"
                    f"（{p.name}）"
                )
        seen: dict[str, int] = {}
        for t in self.topic_stances:
            seen[t.entry_id] = seen.get(t.entry_id, 0) + 1
        for entry_id, count in seen.items():
            if count > 1:
                issues.append(f"topic_stances の entry_id が重複しています: {entry_id}（{count}件）")
        if for_rehearsal:
            if not self.name.strip():
                issues.append("試演には名前が必要です。")
            if not (self.overview.strip() or self.basic_settings.strip()):
                issues.append("試演には概要か基本設定のいずれかに実質的な本文が必要です。")
        return tuple(issues)

    # ---- 変換 ---------------------------------------------------------

    def to_json(self) -> dict[str, Any]:
        """キー順を固定する。revision_id はこの順序に依存する。"""
        return {
            "persona_id": self.persona_id,
            "locale": self.locale,
            "name": self.name,
            "first_person": self.first_person,
            "second_person": self.second_person,
            "species_label": self.species_label,
            "humanity_membership": self.humanity_membership,
            "overview": self.overview,
            "basic_settings": self.basic_settings,
            "speaking_style": self.speaking_style,
            "dialogue_samples": self.dialogue_samples,
            "topic_template_id": self.topic_template_id,
            "topic_stances": [t.to_json() for t in self.topic_stances],
            "person_stances": [p.to_json() for p in self.person_stances],
            "supplements": [s.to_json() for s in self.supplements],
            "extensions": dict(self.extensions),
        }

    @classmethod
    def from_json(cls, raw: Any) -> PersonaDocument:
        if not isinstance(raw, dict):
            raise PersonaError("PersonaDocument はobjectである必要があります")
        extensions = raw.get("extensions") or {}
        if not isinstance(extensions, dict):
            raise PersonaError("extensions はobjectである必要があります")
        return cls(
            persona_id=_text(raw.get("persona_id"), "persona_id"),
            locale=_text(raw.get("locale"), "locale"),
            name=_text(raw.get("name"), "name"),
            first_person=_text(raw.get("first_person"), "first_person"),
            second_person=_text(raw.get("second_person"), "second_person"),
            species_label=_text(raw.get("species_label"), "species_label"),
            humanity_membership=_text(raw.get("humanity_membership"), "humanity_membership"),
            overview=_text(raw.get("overview"), "overview"),
            basic_settings=_text(raw.get("basic_settings"), "basic_settings"),
            speaking_style=_text(raw.get("speaking_style"), "speaking_style"),
            dialogue_samples=_text(raw.get("dialogue_samples"), "dialogue_samples"),
            topic_template_id=_text(raw.get("topic_template_id"), "topic_template_id"),
            topic_stances=tuple(TopicStance.from_json(x) for x in raw.get("topic_stances") or ()),
            person_stances=tuple(PersonStance.from_json(x) for x in raw.get("person_stances") or ()),
            supplements=tuple(Supplement.from_json(x) for x in raw.get("supplements") or ()),
            extensions=dict(extensions),
        )

    def with_changes(self, **changes: Any) -> PersonaDocument:
        return replace(self, **changes)


@dataclass(frozen=True)
class PersonaRecord:
    """人格そのもの。localeごとの文書を束ねる。名前を変えてもIDは変わらない。"""

    persona_id: str
    variants: dict[str, PersonaDocument] = field(default_factory=dict)
    archived: bool = False

    def document(self, locale: str) -> PersonaDocument | None:
        return self.variants.get(locale)

    @property
    def locales(self) -> tuple[str, ...]:
        return tuple(sorted(self.variants))

    def display_name(self, preferred_locale: str = "ja-JP") -> str:
        doc = self.variants.get(preferred_locale)
        if doc is None and self.variants:
            doc = self.variants[sorted(self.variants)[0]]
        return doc.name if doc else self.persona_id

    def to_json(self) -> dict[str, Any]:
        return {
            "persona_id": self.persona_id,
            "archived": self.archived,
            "variants": {loc: self.variants[loc].to_json() for loc in sorted(self.variants)},
        }

    @classmethod
    def from_json(cls, raw: Any) -> PersonaRecord:
        if not isinstance(raw, dict):
            raise PersonaError("PersonaRecord はobjectである必要があります")
        variants_raw = raw.get("variants") or {}
        if not isinstance(variants_raw, dict):
            raise PersonaError("variants はobjectである必要があります")
        return cls(
            persona_id=_text(raw.get("persona_id"), "persona_id"),
            variants={
                _text(loc, "locale"): PersonaDocument.from_json(doc)
                for loc, doc in variants_raw.items()
            },
            archived=bool(raw.get("archived", False)),
        )


@dataclass(frozen=True)
class TopicTemplate:
    """価値観カテゴリの初期テンプレート。`/` を階層表示の区切りにする。"""

    template_id: str
    name: str
    categories: tuple[str, ...] = ()
    source_ref: str = ""

    @property
    def large_categories(self) -> tuple[str, ...]:
        seen: list[str] = []
        for c in self.categories:
            large = c.split("/", 1)[0]
            if large and large not in seen:
                seen.append(large)
        return tuple(seen)

    def duplicate_categories(self) -> tuple[str, ...]:
        seen: dict[str, int] = {}
        for c in self.categories:
            seen[c] = seen.get(c, 0) + 1
        return tuple(c for c, n in seen.items() if n > 1)

    def to_json(self) -> dict[str, Any]:
        return {
            "template_id": self.template_id,
            "name": self.name,
            "categories": list(self.categories),
            "source_ref": self.source_ref,
        }

    @classmethod
    def from_json(cls, raw: Any) -> TopicTemplate:
        if not isinstance(raw, dict):
            raise PersonaError("TopicTemplate はobjectである必要があります")
        return cls(
            template_id=_text(raw.get("template_id"), "template_id"),
            name=_text(raw.get("name"), "name"),
            categories=tuple(_text(c, "categories[]") for c in raw.get("categories") or ()),
            source_ref=_text(raw.get("source_ref"), "source_ref"),
        )


@dataclass(frozen=True)
class ImportReference:
    """取込の記録。取込後に元が変わったかを判定するために残す。

    v0.1 の取込は一方向（読み取り）。書き戻しは行わないため、baseline_projection の
    ような差分反映用の情報は持たない（docs/PHASE_PLAN.md §3）。
    """

    import_id: str
    source_root: str
    locale: str
    imported_at_utc: str
    files: tuple[dict[str, Any], ...] = ()
    persona_ids: tuple[str, ...] = ()

    def to_json(self) -> dict[str, Any]:
        return {
            "import_id": self.import_id,
            "source_root": self.source_root,
            "locale": self.locale,
            "imported_at_utc": self.imported_at_utc,
            "files": [dict(f) for f in self.files],
            "persona_ids": list(self.persona_ids),
        }

    @classmethod
    def from_json(cls, raw: Any) -> ImportReference:
        if not isinstance(raw, dict):
            raise PersonaError("ImportReference はobjectである必要があります")
        return cls(
            import_id=_text(raw.get("import_id"), "import_id"),
            source_root=_text(raw.get("source_root"), "source_root"),
            locale=_text(raw.get("locale"), "locale"),
            imported_at_utc=_text(raw.get("imported_at_utc"), "imported_at_utc"),
            files=tuple(dict(f) for f in raw.get("files") or ()),
            persona_ids=tuple(_text(p, "persona_ids[]") for p in raw.get("persona_ids") or ()),
        )


@dataclass(frozen=True)
class Library:
    """PPRの正規保存。単一JSONとして atomic に書き込む。"""

    schema_version: int = SCHEMA_VERSION
    personas: tuple[PersonaRecord, ...] = ()
    topic_templates: tuple[TopicTemplate, ...] = ()
    imports: tuple[ImportReference, ...] = ()

    def persona(self, persona_id: str) -> PersonaRecord | None:
        return next((p for p in self.personas if p.persona_id == persona_id), None)

    def with_persona(self, record: PersonaRecord) -> Library:
        others = [p for p in self.personas if p.persona_id != record.persona_id]
        others.append(record)
        others.sort(key=lambda p: p.persona_id)
        return replace(self, personas=tuple(others))

    def template(self, template_id: str) -> TopicTemplate | None:
        return next((t for t in self.topic_templates if t.template_id == template_id), None)

    def with_template(self, template: TopicTemplate) -> Library:
        others = [t for t in self.topic_templates if t.template_id != template.template_id]
        others.append(template)
        others.sort(key=lambda t: t.template_id)
        return replace(self, topic_templates=tuple(others))

    def with_import(self, reference: ImportReference) -> Library:
        others = [i for i in self.imports if i.import_id != reference.import_id]
        others.append(reference)
        others.sort(key=lambda i: i.import_id)
        return replace(self, imports=tuple(others))

    def to_json(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "personas": [p.to_json() for p in self.personas],
            "topic_templates": [t.to_json() for t in self.topic_templates],
            "imports": [i.to_json() for i in self.imports],
        }

    @classmethod
    def from_json(cls, raw: Any) -> Library:
        if not isinstance(raw, dict):
            raise PersonaError("library はobjectである必要があります")
        version = raw.get("schema_version")
        if version != SCHEMA_VERSION:
            raise PersonaError(
                f"未対応の schema_version です: {version!r}（対応: {SCHEMA_VERSION}）。"
                "勝手に変換せず、原本を保ったまま停止します。"
            )
        return cls(
            schema_version=SCHEMA_VERSION,
            personas=tuple(PersonaRecord.from_json(x) for x in raw.get("personas") or ()),
            topic_templates=tuple(TopicTemplate.from_json(x) for x in raw.get("topic_templates") or ()),
            imports=tuple(ImportReference.from_json(x) for x in raw.get("imports") or ()),
        )
