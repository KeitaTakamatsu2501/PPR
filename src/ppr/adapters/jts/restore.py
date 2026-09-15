"""取込原本との突き合わせと復元。

取込原本は `data/imports/<id>/files/` にbytesのまま保存されている。
ライブラリ側が壊れても、取り込んだ時点の内容へ戻せる。
元JTSは読まない。原本コピーだけを参照する。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ...domain import ImportReference, Library, PersonaDocument, PersonaRecord

COMPARED_FIELDS = ("overview", "basic_settings", "speaking_style", "dialogue_samples")


@dataclass(frozen=True)
class FieldDifference:
    persona_id: str
    name: str
    field: str
    library_characters: int
    origin_characters: int

    @property
    def delta(self) -> int:
        return self.library_characters - self.origin_characters


def _origin_documents(reference: ImportReference, imports_dir: Path) -> dict[str, dict[str, Any]]:
    base = imports_dir / reference.import_id / "files"
    relative = next(
        (str(f.get("relative_path")) for f in reference.files
         if str(f.get("relative_path", "")).endswith("/characters.json")),
        None,
    )
    if relative is None:
        raise FileNotFoundError(f"取込原本に詳細プロフィールがありません: {reference.import_id}")
    path = base / relative
    if not path.exists():
        raise FileNotFoundError(f"取込原本が見つかりません: {path}")
    raw = json.loads(path.read_text(encoding="utf-8"))
    return {
        str(row.get("character_id")): row
        for row in raw.get("characters") or ()
        if isinstance(row, dict)
    }


def compare_with_origin(
    library: Library, reference: ImportReference, imports_dir: Path
) -> tuple[FieldDifference, ...]:
    """ライブラリと取込原本の本文を突き合わせる。"""
    origins = _origin_documents(reference, imports_dir)
    differences: list[FieldDifference] = []
    for record in library.personas:
        document = record.document(reference.locale)
        if document is None:
            continue
        origin = origins.get(record.persona_id)
        if origin is None:
            continue
        for field in COMPARED_FIELDS:
            current = getattr(document, field)
            source = origin.get(field)
            if not isinstance(source, str) or current == source:
                continue
            differences.append(
                FieldDifference(
                    persona_id=record.persona_id,
                    name=document.name,
                    field=field,
                    library_characters=len(current),
                    origin_characters=len(source),
                )
            )
    return tuple(differences)


def restore_from_origin(
    library: Library,
    reference: ImportReference,
    imports_dir: Path,
    *,
    persona_ids: tuple[str, ...] | None = None,
    fields: tuple[str, ...] = COMPARED_FIELDS,
) -> tuple[Library, tuple[FieldDifference, ...]]:
    """指定した人格の本文を取込原本の内容へ戻す。ほかの項目は触らない。"""
    origins = _origin_documents(reference, imports_dir)
    restored: list[FieldDifference] = []
    updated = library
    for record in library.personas:
        if persona_ids is not None and record.persona_id not in persona_ids:
            continue
        document = record.document(reference.locale)
        origin = origins.get(record.persona_id)
        if document is None or origin is None:
            continue
        changes: dict[str, str] = {}
        for field in fields:
            source = origin.get(field)
            current = getattr(document, field)
            if isinstance(source, str) and current != source:
                changes[field] = source
                restored.append(
                    FieldDifference(
                        persona_id=record.persona_id,
                        name=document.name,
                        field=field,
                        library_characters=len(current),
                        origin_characters=len(source),
                    )
                )
        if not changes:
            continue
        variants = dict(record.variants)
        variants[reference.locale] = document.with_changes(**changes)
        updated = updated.with_persona(
            PersonaRecord(record.persona_id, variants, archived=record.archived)
        )
    return updated, tuple(restored)


def document_from_origin(
    reference: ImportReference, imports_dir: Path, persona_id: str
) -> dict[str, Any] | None:
    return _origin_documents(reference, imports_dir).get(persona_id)
