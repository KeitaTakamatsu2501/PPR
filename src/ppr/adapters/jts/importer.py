"""JTSの人格データをPPRへ取り込む（一方向）。

v0.1 の取込は読み取りのみ。書き出しとJTSへの反映は実装しない
（docs/PHASE_PLAN.md §3）。したがって差分反映用の projection は持たず、
「取込後に元が変わったか」を判定するための hash だけを記録する。

元JTSには一切書き込まない。読み取りの前後でファイルのSHA-256を比較し、
読取中に変化していたら取込を中断する。
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import shutil
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ...domain import (
    ImportReference,
    PersonaDocument,
    PersonaRecord,
    PersonStance,
    TopicStance,
)

FIXED_SCHEMA_VERSIONS = (8,)
DETAIL_SCHEMA_VERSIONS = (2,)
DEFAULT_LOCALE = "ja-JP"

#: 固定プロフィールのうちPPRの人格が引き継ぐ項目。音声関連は引き継がない。
FIXED_PERSONA_FIELDS = (
    "name",
    "first_person",
    "second_person",
    "species_label",
    "humanity_membership",
)


class JtsImportError(RuntimeError):
    """取込を続行できない。正式なlibraryへ部分的に適用しない。"""


@dataclass(frozen=True)
class SourcePair:
    locale: str
    fixed_path: Path
    detail_path: Path
    fixed_relative: str
    detail_relative: str


@dataclass
class ImportResult:
    reference: ImportReference
    records: list[PersonaRecord] = field(default_factory=list)
    diagnostics: list[str] = field(default_factory=list)
    skipped_ids: list[str] = field(default_factory=list)


def locale_paths(jts_root: Path, locale: str) -> SourcePair:
    base = Path(jts_root) / "config" / "high_quality_generation"
    if locale == DEFAULT_LOCALE:
        directory, prefix = base, "config/high_quality_generation"
    else:
        directory = base / "locales" / locale
        prefix = f"config/high_quality_generation/locales/{locale}"
    return SourcePair(
        locale=locale,
        fixed_path=directory / "fixed_characters.json",
        detail_path=directory / "characters.json",
        fixed_relative=f"{prefix}/fixed_characters.json",
        detail_relative=f"{prefix}/characters.json",
    )


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_document(path: Path, label: str) -> dict[str, Any]:
    if not path.exists():
        raise JtsImportError(f"{label} が見つかりません: {path}")
    text = path.read_text(encoding="utf-8")
    try:
        # JSON objectの重複keyを検出する。後勝ちで黙って潰さない。
        value = json.loads(text, object_pairs_hook=_reject_duplicate_keys)
    except ValueError as exc:
        raise JtsImportError(f"{label} をJSONとして読めません: {exc}") from exc
    if not isinstance(value, dict):
        raise JtsImportError(f"{label} のトップレベルがobjectではありません")
    return value


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    seen: dict[str, Any] = {}
    for key, value in pairs:
        if key in seen:
            raise ValueError(f"重複したキーがあります: {key!r}")
        seen[key] = value
    return seen


def _entry_id(persona_id: str, kind: str, index: int) -> str:
    """取込由来の行IDを決定論的に作る。再取込しても同じ行が同じIDになる。"""
    payload = f"{persona_id}|{kind}|{index}".encode("utf-8")
    return f"e-{hashlib.sha256(payload).hexdigest()[:12]}"


def _text(value: Any) -> str:
    """原文のまま受け取る。strip しない。"""
    return value if isinstance(value, str) else ""


def build_document(
    persona_id: str,
    locale: str,
    fixed: dict[str, Any],
    detail: dict[str, Any],
    *,
    topic_template_id: str,
) -> PersonaDocument:
    topics = tuple(
        TopicStance(
            entry_id=_entry_id(persona_id, "topic", index),
            category=_text(row.get("category")),
            stance=_text(row.get("stance")),
        )
        for index, row in enumerate(detail.get("topic_stances") or ())
        if isinstance(row, dict)
    )
    persons = tuple(
        PersonStance(
            entry_id=_entry_id(persona_id, "person", index),
            name=_text(row.get("name")),
            stance=_text(row.get("stance")),
            target_character_id=_text(row.get("target_character_id")),
            direct_address_override=_text(row.get("direct_address_override")),
            perceived_humanity_override=_text(row.get("perceived_humanity_override")),
        )
        for index, row in enumerate(detail.get("person_stances") or ())
        if isinstance(row, dict)
    )
    return PersonaDocument(
        persona_id=persona_id,
        locale=locale,
        name=_text(fixed.get("name")),
        first_person=_text(fixed.get("first_person")),
        second_person=_text(fixed.get("second_person")),
        species_label=_text(fixed.get("species_label")),
        humanity_membership=_text(fixed.get("humanity_membership")),
        overview=_text(detail.get("overview")),
        basic_settings=_text(detail.get("basic_settings")),
        speaking_style=_text(detail.get("speaking_style")),
        dialogue_samples=_text(detail.get("dialogue_samples")),
        topic_template_id=topic_template_id,
        topic_stances=topics,
        person_stances=persons,
    )


def import_locale(
    jts_root: Path,
    locale: str,
    *,
    persona_ids: tuple[str, ...],
    imports_dir: Path,
    topic_template_id: str = "jts-194-v1",
) -> ImportResult:
    """指定した人格だけをPPRへ取り込む。

    `persona_ids` に含まれないレコードは読み飛ばす。資産化の対象外は取り込まない
    （docs/PHASE_PLAN.md §3）。
    """
    pair = locale_paths(Path(jts_root), locale)
    before = {
        pair.fixed_relative: sha256_of(pair.fixed_path) if pair.fixed_path.exists() else None,
        pair.detail_relative: sha256_of(pair.detail_path) if pair.detail_path.exists() else None,
    }
    if before[pair.fixed_relative] is None or before[pair.detail_relative] is None:
        missing = [rel for rel, h in before.items() if h is None]
        raise JtsImportError(
            "固定プロフィールと詳細プロフィールは対で必要です。"
            f"見つからないファイル: {missing}"
        )

    fixed_doc = _load_document(pair.fixed_path, "固定プロフィール")
    detail_doc = _load_document(pair.detail_path, "詳細プロフィール")

    fixed_version = fixed_doc.get("schema_version")
    detail_version = detail_doc.get("schema_version")
    if fixed_version not in FIXED_SCHEMA_VERSIONS:
        raise JtsImportError(
            f"未対応の固定プロフィール schema_version です: {fixed_version!r}。"
            "原本を保持したまま中断します。編集可能な形式へ勝手に変換しません。"
        )
    if detail_version not in DETAIL_SCHEMA_VERSIONS:
        raise JtsImportError(
            f"未対応の詳細プロフィール schema_version です: {detail_version!r}。"
            "原本を保持したまま中断します。編集可能な形式へ勝手に変換しません。"
        )

    fixed_rows = fixed_doc.get("characters")
    detail_rows = detail_doc.get("characters")
    if not isinstance(fixed_rows, list) or not isinstance(detail_rows, list):
        raise JtsImportError("characters が配列ではありません")

    fixed_by_id: dict[str, dict[str, Any]] = {}
    for row in fixed_rows:
        if not isinstance(row, dict):
            continue
        row_id = _text(row.get("id"))
        if not row_id:
            continue
        if row_id in fixed_by_id:
            raise JtsImportError(f"固定プロフィールにIDの重複があります: {row_id}")
        fixed_by_id[row_id] = row

    detail_by_id: dict[str, dict[str, Any]] = {}
    for row in detail_rows:
        if not isinstance(row, dict):
            continue
        row_id = _text(row.get("character_id"))
        if not row_id:
            continue
        if row_id in detail_by_id:
            raise JtsImportError(f"詳細プロフィールにIDの重複があります: {row_id}")
        detail_by_id[row_id] = row

    orphans = sorted(set(detail_by_id) - set(fixed_by_id))
    if orphans:
        raise JtsImportError(f"固定プロフィールに存在しない詳細IDがあります: {orphans}")

    diagnostics: list[str] = []
    skipped: list[str] = []
    documents: list[PersonaDocument] = []
    for persona_id in persona_ids:
        if persona_id not in fixed_by_id:
            skipped.append(persona_id)
            diagnostics.append(f"固定プロフィールに存在しません: {persona_id}")
            continue
        if persona_id not in detail_by_id:
            skipped.append(persona_id)
            diagnostics.append(f"詳細プロフィールに存在しません: {persona_id}")
            continue
        documents.append(
            build_document(
                persona_id,
                locale,
                fixed_by_id[persona_id],
                detail_by_id[persona_id],
                topic_template_id=topic_template_id,
            )
        )

    after = {
        pair.fixed_relative: sha256_of(pair.fixed_path),
        pair.detail_relative: sha256_of(pair.detail_path),
    }
    changed = [rel for rel in before if before[rel] != after[rel]]
    if changed:
        raise JtsImportError(
            f"読み取り中に元ファイルが変化しました: {changed}。取込を中断します。"
        )

    import_id = f"imp-{_dt.datetime.now(_dt.timezone.utc):%Y%m%dT%H%M%SZ}-{uuid.uuid4().hex[:8]}"
    files_meta = _copy_originals(pair, imports_dir / import_id, after)

    reference = ImportReference(
        import_id=import_id,
        source_root=str(Path(jts_root).resolve()),
        locale=locale,
        imported_at_utc=_dt.datetime.now(_dt.timezone.utc).isoformat(),
        files=tuple(files_meta),
        persona_ids=tuple(d.persona_id for d in documents),
    )
    records = [
        PersonaRecord(persona_id=d.persona_id, variants={locale: d}) for d in documents
    ]
    return ImportResult(
        reference=reference, records=records, diagnostics=diagnostics, skipped_ids=skipped
    )


def _copy_originals(pair: SourcePair, target_dir: Path, hashes: dict[str, str]) -> list[dict[str, Any]]:
    """取込原本をbytesのまま保存する。libraryには埋め込まない。"""
    meta: list[dict[str, Any]] = []
    for source, relative in ((pair.fixed_path, pair.fixed_relative), (pair.detail_path, pair.detail_relative)):
        destination = target_dir / "files" / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
        meta.append(
            {
                "relative_path": relative,
                "sha256": hashes[relative],
                "size_bytes": source.stat().st_size,
                "stored_at": str(destination.relative_to(target_dir)),
            }
        )
    manifest = target_dir / "manifest.json"
    manifest.write_text(
        json.dumps({"locale": pair.locale, "files": meta}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return meta


def source_drift(reference: ImportReference, jts_root: Path) -> tuple[str, ...]:
    """取込後に元ファイルが変わったかを返す。二重管理の可視化に使う。"""
    drifted: list[str] = []
    for entry in reference.files:
        relative = str(entry.get("relative_path", ""))
        recorded = str(entry.get("sha256", ""))
        path = Path(jts_root) / relative
        if not path.exists():
            drifted.append(f"{relative}: 見つかりません")
            continue
        current = sha256_of(path)
        if current != recorded:
            drifted.append(f"{relative}: 取込時 {recorded[:12]}… → 現在 {current[:12]}…")
    return tuple(drifted)
