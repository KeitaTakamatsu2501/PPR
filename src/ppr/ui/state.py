"""編集状態。Tkに依存しない純粋なロジックだけを置く。

GUIはこの状態を表示し、編集操作をここへ委ねる。保存前の変更は
このオブジェクトの中だけに存在し、ライブラリへは `commit` でのみ反映する。
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any

from ..domain import (
    Library,
    PersonaDocument,
    PersonaRecord,
    PersonStance,
    TopicStance,
    new_entry_id,
)
from ..revisions import revision_id_for
from ..sections import BodySection, replace_section_text, split_sections

BODY_FIELDS: tuple[tuple[str, str], ...] = (
    ("overview", "概要"),
    ("basic_settings", "基本設定"),
    ("speaking_style", "口調・話し方"),
    ("dialogue_samples", "セリフのサンプル"),
)
BODY_FIELD_NAMES = tuple(name for name, _ in BODY_FIELDS)
BODY_FIELD_LABELS = dict(BODY_FIELDS)

BASIC_FIELDS: tuple[tuple[str, str], ...] = (
    ("name", "名前"),
    ("first_person", "一人称"),
    ("second_person", "二人称"),
    ("species_label", "種族・存在種別"),
    ("humanity_membership", "人類との関係"),
)


@dataclass(frozen=True)
class EditTarget:
    """編集中のウィジェットが「どの人格のどこ」を映しているかの束縛。

    GUIの再描画はウィジェットの中身を差し替えるが、その際に発火する変更イベントが
    古い内容を新しい人格へ書き戻す事故が起きうる。書き込みの直前にこの束縛を
    現在の状態と照合し、一致しなければ書き込まない。
    """

    persona_id: str
    locale: str
    kind: str
    field: str = ""
    index: int = -1
    entry_id: str = ""

    NONE: "EditTarget | None" = None

    def matches(self, state: "EditorState") -> bool:
        return self.persona_id == state.persona_id and self.locale == state.locale


@dataclass
class EditorState:
    """1つの人格文書に対する編集セッション。"""

    library: Library
    persona_id: str
    locale: str
    document: PersonaDocument
    baseline_revision: str

    @classmethod
    def open(cls, library: Library, persona_id: str, locale: str) -> EditorState:
        record = library.persona(persona_id)
        if record is None:
            raise KeyError(f"人格が見つかりません: {persona_id}")
        document = record.document(locale)
        if document is None:
            raise KeyError(f"localeが見つかりません: {persona_id} / {locale}")
        return cls(
            library=library,
            persona_id=persona_id,
            locale=locale,
            document=document,
            baseline_revision=revision_id_for(document),
        )

    # ---- 状態 ---------------------------------------------------------

    @property
    def dirty(self) -> bool:
        return revision_id_for(self.document) != self.baseline_revision

    @property
    def current_revision(self) -> str:
        return revision_id_for(self.document)

    # ---- 基本項目 -----------------------------------------------------

    def set_basic(self, field: str, value: str) -> None:
        if field not in dict(BASIC_FIELDS):
            raise KeyError(f"未知の基本項目です: {field}")
        self.document = self.document.with_changes(**{field: value})

    # ---- 本文 ---------------------------------------------------------

    def body(self, field: str) -> str:
        if field not in BODY_FIELD_NAMES:
            raise KeyError(f"未知の本文です: {field}")
        return getattr(self.document, field)

    def sections(self, field: str) -> tuple[BodySection, ...]:
        return split_sections(self.body(field))

    def set_body(self, field: str, value: str) -> None:
        if field not in BODY_FIELD_NAMES:
            raise KeyError(f"未知の本文です: {field}")
        self.document = self.document.with_changes(**{field: value})

    def set_section(self, field: str, index: int, value: str) -> None:
        """指定区画だけを差し替える。他の区画は1文字も変えない。"""
        self.set_body(field, replace_section_text(self.body(field), index, value))

    # ---- 価値観 -------------------------------------------------------

    def set_topic_stance(self, entry_id: str, stance: str) -> None:
        rows = []
        found = False
        for row in self.document.topic_stances:
            if row.entry_id == entry_id:
                rows.append(replace(row, stance=stance))
                found = True
            else:
                rows.append(row)
        if not found:
            raise KeyError(f"価値観の行が見つかりません: {entry_id}")
        self.document = self.document.with_changes(topic_stances=tuple(rows))

    def rename_topic_category(self, entry_id: str, category: str) -> None:
        rows = [
            replace(row, category=category) if row.entry_id == entry_id else row
            for row in self.document.topic_stances
        ]
        self.document = self.document.with_changes(topic_stances=tuple(rows))

    def add_topic_stance(self, category: str, stance: str = "") -> str:
        entry_id = new_entry_id()
        rows = (*self.document.topic_stances, TopicStance(entry_id, category, stance))
        self.document = self.document.with_changes(topic_stances=rows)
        return entry_id

    def remove_topic_stance(self, entry_id: str) -> TopicStance:
        """本文を持つ行の削除は呼び出し側が明示確認すること。"""
        target = next((r for r in self.document.topic_stances if r.entry_id == entry_id), None)
        if target is None:
            raise KeyError(f"価値観の行が見つかりません: {entry_id}")
        rows = tuple(r for r in self.document.topic_stances if r.entry_id != entry_id)
        self.document = self.document.with_changes(topic_stances=rows)
        return target

    def move_topic_stance(self, entry_id: str, offset: int) -> None:
        rows = list(self.document.topic_stances)
        index = next((i for i, r in enumerate(rows) if r.entry_id == entry_id), None)
        if index is None:
            raise KeyError(f"価値観の行が見つかりません: {entry_id}")
        target = max(0, min(len(rows) - 1, index + offset))
        if target == index:
            return
        rows.insert(target, rows.pop(index))
        self.document = self.document.with_changes(topic_stances=tuple(rows))

    def grouped_topics(self) -> list[tuple[str, list[TopicStance]]]:
        """大分類ごとにまとめる。「恒常的な価値境界」は先頭へ固定する。"""
        boundary = [r for r in self.document.topic_stances if r.is_value_boundary]
        groups: dict[str, list[TopicStance]] = {}
        order: list[str] = []
        for row in self.document.topic_stances:
            if row.is_value_boundary:
                continue
            key = row.large_category
            if key not in groups:
                groups[key] = []
                order.append(key)
            groups[key].append(row)
        result: list[tuple[str, list[TopicStance]]] = []
        if boundary:
            result.append(("恒常的な価値境界", boundary))
        result.extend((key, groups[key]) for key in order)
        return result

    # ---- 人物関係 -----------------------------------------------------

    def set_person_stance(self, entry_id: str, **changes: Any) -> None:
        allowed = {
            "name", "stance", "target_character_id",
            "direct_address_override", "perceived_humanity_override",
        }
        unknown = set(changes) - allowed
        if unknown:
            raise KeyError(f"未知の項目です: {sorted(unknown)}")
        rows = []
        found = False
        for row in self.document.person_stances:
            if row.entry_id == entry_id:
                rows.append(replace(row, **changes))
                found = True
            else:
                rows.append(row)
        if not found:
            raise KeyError(f"人物関係の行が見つかりません: {entry_id}")
        self.document = self.document.with_changes(person_stances=tuple(rows))

    def add_person_stance(self, name: str = "", target_character_id: str = "") -> str:
        entry_id = new_entry_id()
        rows = (
            *self.document.person_stances,
            PersonStance(entry_id=entry_id, name=name, stance="", target_character_id=target_character_id),
        )
        self.document = self.document.with_changes(person_stances=rows)
        return entry_id

    def remove_person_stance(self, entry_id: str) -> PersonStance:
        target = next((r for r in self.document.person_stances if r.entry_id == entry_id), None)
        if target is None:
            raise KeyError(f"人物関係の行が見つかりません: {entry_id}")
        rows = tuple(r for r in self.document.person_stances if r.entry_id != entry_id)
        self.document = self.document.with_changes(person_stances=rows)
        return target

    def unresolved_person_targets(self) -> tuple[PersonStance, ...]:
        """PPR内に存在しない相手を指す関係。保持はするが、その旨を表示する。"""
        known = {p.persona_id for p in self.library.personas}
        return tuple(
            row
            for row in self.document.person_stances
            if row.target_character_id and row.target_character_id not in known
        )

    # ---- 束縛つきの書き込み -------------------------------------------

    def target_for_section(self, field: str, index: int) -> EditTarget:
        return EditTarget(self.persona_id, self.locale, "body", field=field, index=index)

    def target_for_topic(self, entry_id: str) -> EditTarget:
        return EditTarget(self.persona_id, self.locale, "topic", entry_id=entry_id)

    def target_for_person(self, entry_id: str) -> EditTarget:
        return EditTarget(self.persona_id, self.locale, "person", entry_id=entry_id)

    def apply_section(self, target: EditTarget | None, value: str) -> bool:
        """束縛が現在の状態と一致するときだけ書き込む。戻り値は実際に変更したか。"""
        if target is None or target.kind != "body" or not target.matches(self):
            return False
        if target.field not in BODY_FIELD_NAMES:
            return False
        sections = self.sections(target.field)
        if not 0 <= target.index < len(sections):
            return False
        if sections[target.index].text == value:
            return False
        self.set_section(target.field, target.index, value)
        return True

    def apply_topic(self, target: EditTarget | None, value: str) -> bool:
        if target is None or target.kind != "topic" or not target.matches(self):
            return False
        row = next((r for r in self.document.topic_stances if r.entry_id == target.entry_id), None)
        if row is None or row.stance == value:
            return False
        self.set_topic_stance(target.entry_id, value)
        return True

    def apply_person(self, target: EditTarget | None, changes: dict[str, str]) -> bool:
        if target is None or target.kind != "person" or not target.matches(self):
            return False
        row = next((r for r in self.document.person_stances if r.entry_id == target.entry_id), None)
        if row is None:
            return False
        if all(getattr(row, key) == value for key, value in changes.items()):
            return False
        self.set_person_stance(target.entry_id, **changes)
        return True

    # ---- 反映 ---------------------------------------------------------

    def commit(self) -> Library:
        """編集内容をライブラリへ載せる。他のlocaleと他の人格は変えない。"""
        record = self.library.persona(self.persona_id)
        if record is None:
            raise KeyError(f"人格が見つかりません: {self.persona_id}")
        variants = dict(record.variants)
        variants[self.locale] = self.document
        updated = PersonaRecord(
            persona_id=record.persona_id, variants=variants, archived=record.archived
        )
        self.library = self.library.with_persona(updated)
        self.baseline_revision = revision_id_for(self.document)
        return self.library
