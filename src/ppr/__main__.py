"""PPR のエントリポイント。

通常起動でJTS/MyFriends/音声探索/ネットワークを呼ばない。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .assets import ASSET_PERSONA_IDS, ASSET_PERSONA_NAMES
from .config import resolve_config
from .domain import Library
from .storage import LibraryLock, LibraryLockedError, LibraryStore
from .templates import DEFAULT_TEMPLATE_ID, load_bundled_template


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ppr", description="人格ビルダー")
    parser.add_argument("--data-dir", default=None, help="データ置き場（既定: PPR直下の data/）")
    parser.add_argument("--offline", action="store_true",
                        help="ネットワークと資格情報解決を一切呼ばない")
    sub = parser.add_subparsers(dest="command")

    imp = sub.add_parser("import-jts", help="JTSから人格を取り込む（一方向・読み取りのみ）")
    imp.add_argument("--jts-root", required=True)
    imp.add_argument("--locale", default="ja-JP")
    imp.add_argument("--persona-ids", nargs="*", default=None,
                     help="既定は資産化対象の8人")

    sub.add_parser("list", help="取り込み済みの人格を一覧する")
    sub.add_parser("check-drift", help="取込後にJTS側が変化したかを調べる")
    return parser


def cmd_import(args: argparse.Namespace) -> int:
    from .adapters.jts.importer import JtsImportError, import_locale

    config = resolve_config(args.data_dir, offline=args.offline)
    config.ensure_directories()
    persona_ids = tuple(args.persona_ids) if args.persona_ids else ASSET_PERSONA_IDS

    store = LibraryStore(config)
    try:
        with LibraryLock(config):
            library = store.load()
            try:
                result = import_locale(
                    Path(args.jts_root),
                    args.locale,
                    persona_ids=persona_ids,
                    imports_dir=config.imports_dir,
                    topic_template_id=DEFAULT_TEMPLATE_ID,
                )
            except JtsImportError as exc:
                print(f"取込を中断しました: {exc}", file=sys.stderr)
                return 1

            if library.template(DEFAULT_TEMPLATE_ID) is None:
                library = library.with_template(load_bundled_template())
            for record in result.records:
                existing = library.persona(record.persona_id)
                if existing is not None:
                    variants = dict(existing.variants)
                    variants.update(record.variants)
                    record = existing.__class__(
                        persona_id=record.persona_id, variants=variants, archived=existing.archived
                    )
                library = library.with_persona(record)
            library = library.with_import(result.reference)
            store.save(library)
    except LibraryLockedError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    print(f"取込ID: {result.reference.import_id}")
    print(f"locale: {result.reference.locale}   取込元: {result.reference.source_root}")
    for record in result.records:
        doc = record.variants[args.locale]
        print(
            f"  {doc.name:<8} {doc.persona_id:<24} "
            f"本文{doc.body_characters:>6}字 / トピック{len(doc.topic_stances):>4}件 "
            f"/ 人物関係{len(doc.person_stances):>2}件"
        )
    for note in result.diagnostics:
        print(f"  [診断] {note}")
    print(f"取込原本: {config.imports_dir / result.reference.import_id}")
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    config = resolve_config(args.data_dir, offline=args.offline)
    library = LibraryStore(config).load()
    if not library.personas:
        print("人格がありません。`ppr import-jts --jts-root <path>` で取り込んでください。")
        return 0
    print(f"{'名前':<10}{'persona_id':<26}{'locale':<20}{'トピック':>8}{'関係':>6}{'本文字数':>9}")
    for record in library.personas:
        for locale in record.locales:
            doc = record.variants[locale]
            print(
                f"{doc.name:<10}{doc.persona_id:<26}{locale:<20}"
                f"{len(doc.topic_stances):>8}{len(doc.person_stances):>6}{doc.body_characters:>9}"
            )
    return 0


def cmd_check_drift(args: argparse.Namespace) -> int:
    from .adapters.jts.importer import source_drift

    config = resolve_config(args.data_dir, offline=args.offline)
    library = LibraryStore(config).load()
    if not library.imports:
        print("取込の記録がありません。")
        return 0
    any_drift = False
    for reference in library.imports:
        drifted = source_drift(reference, Path(reference.source_root))
        status = "変化あり" if drifted else "変化なし"
        print(f"{reference.import_id}  {reference.locale}  {status}")
        for line in drifted:
            any_drift = True
            print(f"    {line}")
    return 1 if any_drift else 0


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.command == "import-jts":
        return cmd_import(args)
    if args.command == "list":
        return cmd_list(args)
    if args.command == "check-drift":
        return cmd_check_drift(args)

    from .ui.app import run_app

    config = resolve_config(args.data_dir, offline=args.offline)
    return run_app(config)


if __name__ == "__main__":
    raise SystemExit(main())
