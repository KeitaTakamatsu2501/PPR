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

    reh = sub.add_parser("rehearse", help="試演を1件実行する")
    reh.add_argument("--scheme", default="jts-dialogue", choices=["jts-dialogue", "sbt-thread"])
    reh.add_argument("--a", required=True, help="話者AのpersonaID")
    reh.add_argument("--b", required=True, help="話者BのpersonaID")
    reh.add_argument("--locale", default="ja-JP")
    reh.add_argument("--topic", default=None)
    reh.add_argument("--material", default=None)
    reh.add_argument("--lm-studio", action="store_true", help="LM Studio で実行する")
    reh.add_argument("--url", default="http://127.0.0.1:1234")
    reh.add_argument("--model-contains", default="gemma")
    reh.add_argument("--utterances", type=int, default=6, help="jts-dialogue の発言数")
    reh.add_argument("--total-posts", type=int, default=6, help="sbt-thread の合計件数")
    reh.add_argument("--temperature", type=float, default=0.7)
    reh.add_argument("--context-limit", type=int, default=None,
                     help="既定はLM Studioの報告値。Fakeでは200000")
    reh.add_argument("--no-category-selection", action="store_true",
                     help="小カテゴリのモデル選択を行わない")

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


DEFAULT_TOPIC = (
    "架空の町で、湿地を埋めて道路を延ばすと通勤が15分短くなる。"
    "「反対する人は町の成長を邪魔している」という主張をどう見るか。"
)
DEFAULT_MATERIAL = "反対する人は町の成長を邪魔している。"


def cmd_rehearse(args: argparse.Namespace) -> int:
    import json as _json

    from .revisions import RevisionStore, revision_id_for
    from .rehearsal.contracts import (
        SELECTION_MODEL,
        SELECTION_NONE,
        CastMember,
        ContextOptions,
        ModelSettings,
        RehearsalRequest,
        Scenario,
    )
    from .rehearsal.runner import RehearsalRunner
    from .rehearsal.schemes import create_scheme

    config = resolve_config(args.data_dir, offline=args.offline)
    config.ensure_directories()
    library = LibraryStore(config).load()

    documents = {}
    for ref, persona_id in (("a", args.a), ("b", args.b)):
        record = library.persona(persona_id)
        if record is None:
            print(f"人格が見つかりません: {persona_id}", file=sys.stderr)
            return 1
        document = record.document(args.locale)
        if document is None:
            print(f"localeがありません: {persona_id} / {args.locale}", file=sys.stderr)
            return 1
        documents[ref] = document

    if args.lm_studio:
        if args.offline:
            print("--offline では実モデルへ接続できません。", file=sys.stderr)
            return 1
        from .llm.lm_studio import LmStudioClient, LmStudioError

        client = LmStudioClient(args.url)
        try:
            models = client.list_models()
        except LmStudioError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        if not models:
            print(f"LM Studio にモデルがありません: {args.url}", file=sys.stderr)
            return 1
        needle = args.model_contains.lower()
        picked = [m for m in models if needle in m.model_key.lower()] or models
        chosen = next((m for m in picked if m.loaded), picked[0])
        print("LM Studio のモデル:")
        for model in models:
            mark = "→" if model is chosen else " "
            print(f"  {mark} {model.display_name}")
        context_limit = args.context_limit or chosen.context_length or 32768
        settings = ModelSettings(
            provider="lm-studio", model_key=chosen.model_key,
            inference_id=chosen.inference_id, temperature=args.temperature,
            max_tokens=4096, context_limit=context_limit,
        )
    else:
        from .llm.fake import FakeLanguageModelClient
        from .rehearsal.runner import CATEGORY_SELECTION_SCHEMA_NAME

        lines = {
            "lines": [
                {"speaker": s, "text": f"（Fake）{i + 1}番目の発言。", "emotion": "normal"}
                for i, s in enumerate(("a", "b") * 6)
            ][: args.utterances]
        }
        client = FakeLanguageModelClient(
            {
                CATEGORY_SELECTION_SCHEMA_NAME: {"categories": []},
                "jts_dialogue_lines": lines,
            },
            default={"text": "（Fake）これは投稿の本文です。" * 4},
        )
        settings = ModelSettings(context_limit=args.context_limit or 200000)
        print("Fake provider で実行します。実モデルの結果ではありません。")

    revisions = RevisionStore(config.revisions_dir)
    cast = tuple(
        CastMember(ref, documents[ref].persona_id, args.locale,
                   revisions.create(documents[ref]).revision_id)
        for ref in ("a", "b")
    )
    scheme_input = (
        {"utterances": args.utterances} if args.scheme == "jts-dialogue"
        else {"total_posts": args.total_posts}
    )
    request = RehearsalRequest(
        scheme_id=args.scheme, scheme_version=1, cast=cast,
        scenario=Scenario(
            topic=args.topic or DEFAULT_TOPIC,
            material=args.material if args.material is not None else DEFAULT_MATERIAL,
        ),
        model_settings=settings,
        context_options=ContextOptions(
            max_persona_characters=None,
            category_selection=SELECTION_NONE if args.no_category_selection else SELECTION_MODEL,
        ),
        scheme_input=scheme_input,
    )

    scheme = create_scheme(args.scheme)
    print(f"\n{documents['a'].name} × {documents['b'].name}   {scheme.display_name}")
    print(f"話題: {request.scenario.topic[:60]}…\n")
    result = RehearsalRunner(
        library, client, revisions=revisions, on_status=lambda m: print(f"  · {m}")
    ).run(request, scheme)

    runs_dir = config.data_dir / "runs"
    runs_dir.mkdir(parents=True, exist_ok=True)
    path = runs_dir / f"{result.run_id}.json"
    path.write_text(
        _json.dumps(result.to_json(), ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print(f"\n状態: {result.status}   段階 {len(result.stages)} 件")
    for warning in result.warnings:
        print(f"  [警告] {warning}")
    for error in result.errors:
        print(f"  [エラー] {error}")
    names = {c.actor_ref: c.name for c in result.persona_contexts}
    for utterance in result.utterances:
        print(f"\n──[{utterance.utterance_id}] {names.get(utterance.actor_ref, utterance.actor_ref)}")
        print(utterance.text)
    print(f"\n記録: {path}")
    return 0 if result.succeeded else 1


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
    if args.command == "rehearse":
        return cmd_rehearse(args)

    from .ui.app import run_app

    config = resolve_config(args.data_dir, offline=args.offline)
    return run_app(config)


if __name__ == "__main__":
    raise SystemExit(main())
