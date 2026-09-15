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
    sub.add_parser("diff-origin", help="ライブラリと取込原本の本文を突き合わせる")

    res = sub.add_parser("restore-origin", help="本文を取込原本の内容へ戻す")
    res.add_argument("--persona", nargs="*", default=None, help="既定は差分のあるすべて")
    res.add_argument("--field", nargs="*", default=None, help="既定は4本文すべて")
    res.add_argument("--yes", action="store_true", help="確認せずに実行する")

    sub.add_parser(
        "selftest-text",
        help="Tk Textの往復で本文が変化しないかを、保存済みの全人格で確かめる",
    )
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


def _format_differences(differences) -> None:
    print(f"{'名前':<10}{'項目':<18}{'ライブラリ':>10}{'原本':>8}{'差':>9}")
    for d in differences:
        print(f"{d.name:<10}{d.field:<18}{d.library_characters:>10}{d.origin_characters:>8}{d.delta:>+9}")


def cmd_diff_origin(args: argparse.Namespace) -> int:
    from .adapters.jts.restore import compare_with_origin

    config = resolve_config(args.data_dir, offline=args.offline)
    library = LibraryStore(config).load()
    if not library.imports:
        print("取込の記録がありません。")
        return 0
    reference = library.imports[-1]
    differences = compare_with_origin(library, reference, config.imports_dir)
    print(f"取込原本: {reference.import_id}  ({reference.locale})")
    if not differences:
        print("本文の差分はありません。")
        return 0
    _format_differences(differences)
    print(f"\n差分 {len(differences)} 件。取込後の編集か、破損の可能性があります。")
    print("戻す場合:  python -m ppr restore-origin --persona <persona_id>")
    return 1


def cmd_restore_origin(args: argparse.Namespace) -> int:
    from .adapters.jts.restore import COMPARED_FIELDS, restore_from_origin

    config = resolve_config(args.data_dir, offline=args.offline)
    store = LibraryStore(config)
    try:
        with LibraryLock(config):
            library = store.load()
            if not library.imports:
                print("取込の記録がありません。", file=sys.stderr)
                return 1
            reference = library.imports[-1]
            fields = tuple(args.field) if args.field else COMPARED_FIELDS
            personas = tuple(args.persona) if args.persona else None
            updated, restored = restore_from_origin(
                library, reference, config.imports_dir, persona_ids=personas, fields=fields
            )
            if not restored:
                print("戻す対象がありません。")
                return 0
            print("次の本文を取込原本の内容へ戻します。")
            _format_differences(restored)
            if not args.yes:
                answer = input("\n実行しますか [y/N]: ").strip().lower()
                if answer not in ("y", "yes"):
                    print("中止しました。")
                    return 1
            store.save(updated)
    except LibraryLockedError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(f"\n{len(restored)} 件を戻しました。")
    return 0


def cmd_selftest_text(args: argparse.Namespace) -> int:
    """編集ウィジェットへ入れて取り出すと文字が変わらないかを実データで確かめる。

    Tk Text は末尾に改行を1つ強制する。取得を誤ると、編集していない本文へ
    改行が足されたり、末尾の空行が失われたりする。実際に本文末尾へ改行が
    1文字増えた事例があったため、この経路を実データで検査する。
    """
    import tkinter as tk

    from .sections import split_sections
    from .ui.app import text_value

    config = resolve_config(args.data_dir, offline=args.offline)
    library = LibraryStore(config).load()
    if not library.personas:
        print("人格がありません。")
        return 0

    root = tk.Tk()
    root.withdraw()
    widget = tk.Text(root)
    failures = 0
    checked = 0
    for record in library.personas:
        for locale in record.locales:
            document = record.variants[locale]
            for field in ("overview", "basic_settings", "speaking_style", "dialogue_samples"):
                body = getattr(document, field)
                for index, section in enumerate(split_sections(body)):
                    widget.delete("1.0", "end")
                    widget.insert("1.0", section.text)
                    got = text_value(widget)
                    checked += 1
                    if got != section.text:
                        failures += 1
                        print(
                            f"  差異: {document.name} / {field} / 区画{index}  "
                            f"入力{len(section.text)}字 → 取得{len(got)}字  "
                            f"末尾 {section.text[-8:]!r} → {got[-8:]!r}"
                        )
    root.destroy()
    print(f"\n検査した区画: {checked}")
    if failures:
        print(f"往復で変化した区画: {failures} 件。編集画面は本文を変えてしまいます。")
        return 1
    print("往復で変化した区画はありません。")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.command == "import-jts":
        return cmd_import(args)
    if args.command == "list":
        return cmd_list(args)
    if args.command == "check-drift":
        return cmd_check_drift(args)
    if args.command == "diff-origin":
        return cmd_diff_origin(args)
    if args.command == "restore-origin":
        return cmd_restore_origin(args)
    if args.command == "selftest-text":
        return cmd_selftest_text(args)

    from .ui.app import run_app

    config = resolve_config(args.data_dir, offline=args.offline)
    return run_app(config)


if __name__ == "__main__":
    raise SystemExit(main())
