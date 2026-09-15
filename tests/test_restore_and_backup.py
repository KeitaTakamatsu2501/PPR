import json
import tempfile
import unittest
from pathlib import Path

from ppr.adapters.jts.importer import import_locale, locale_paths
from ppr.adapters.jts.restore import compare_with_origin, restore_from_origin
from ppr.config import AppConfig
from ppr.domain import Library, PersonaDocument, PersonaRecord
from ppr.storage import BACKUP_GENERATIONS, LibraryStore

from .test_jts_import import default_detail, default_fixed, write_pair


def build(tmp: Path):
    root = tmp / "jts"
    write_pair(root, fixed=default_fixed(), detail=default_detail())
    result = import_locale(root, "ja-JP", persona_ids=("aaa-1",), imports_dir=tmp / "imports")
    library = Library()
    for record in result.records:
        library = library.with_persona(record)
    return library.with_import(result.reference), result.reference, tmp / "imports"


class RestoreFromOrigin(unittest.TestCase):
    def test_no_difference_right_after_import(self):
        with tempfile.TemporaryDirectory() as t:
            library, reference, imports = build(Path(t))
            self.assertEqual(compare_with_origin(library, reference, imports), ())

    def test_truncation_is_detected(self):
        with tempfile.TemporaryDirectory() as t:
            library, reference, imports = build(Path(t))
            record = library.persona("aaa-1")
            doc = record.variants["ja-JP"]
            # 先頭だけ残して切り詰める（実際に起きた破損と同じ形）
            self.assertGreater(len(doc.basic_settings), 5)
            broken = doc.with_changes(basic_settings=doc.basic_settings[:5])
            library = library.with_persona(PersonaRecord("aaa-1", {"ja-JP": broken}))
            differences = compare_with_origin(library, reference, imports)
            self.assertEqual(len(differences), 1)
            self.assertEqual(differences[0].field, "basic_settings")
            self.assertLess(differences[0].delta, 0)

    def test_restore_brings_back_the_exact_original(self):
        with tempfile.TemporaryDirectory() as t:
            library, reference, imports = build(Path(t))
            original = library.persona("aaa-1").variants["ja-JP"].basic_settings
            broken = library.persona("aaa-1").variants["ja-JP"].with_changes(basic_settings="壊れた")
            library = library.with_persona(PersonaRecord("aaa-1", {"ja-JP": broken}))
            restored, changes = restore_from_origin(library, reference, imports)
            self.assertEqual(len(changes), 1)
            self.assertEqual(restored.persona("aaa-1").variants["ja-JP"].basic_settings, original)
            self.assertEqual(compare_with_origin(restored, reference, imports), ())

    def test_restore_only_touches_requested_fields(self):
        with tempfile.TemporaryDirectory() as t:
            library, reference, imports = build(Path(t))
            doc = library.persona("aaa-1").variants["ja-JP"]
            broken = doc.with_changes(basic_settings="壊れた", overview="こちらは作者の編集")
            library = library.with_persona(PersonaRecord("aaa-1", {"ja-JP": broken}))
            restored, _ = restore_from_origin(
                library, reference, imports, fields=("basic_settings",)
            )
            got = restored.persona("aaa-1").variants["ja-JP"]
            self.assertEqual(got.overview, "こちらは作者の編集")
            self.assertNotEqual(got.basic_settings, "壊れた")

    def test_restore_does_not_touch_other_personas(self):
        with tempfile.TemporaryDirectory() as t:
            library, reference, imports = build(Path(t))
            other = PersonaDocument(persona_id="ppr-x", locale="ja-JP", name="他人", overview="無関係")
            library = library.with_persona(PersonaRecord("ppr-x", {"ja-JP": other}))
            restored, _ = restore_from_origin(library, reference, imports)
            self.assertEqual(restored.persona("ppr-x").variants["ja-JP"].overview, "無関係")


class Backups(unittest.TestCase):
    def _doc(self, text: str) -> Library:
        return Library().with_persona(
            PersonaRecord("ppr-1", {"ja-JP": PersonaDocument(
                persona_id="ppr-1", locale="ja-JP", name="テスト", overview=text)})
        )

    def test_each_save_leaves_a_generation(self):
        with tempfile.TemporaryDirectory() as t:
            store = LibraryStore(AppConfig(data_dir=Path(t)))
            for i in range(3):
                store.save(self._doc(f"版{i}"))
            backups = sorted(store.backups_dir().glob("library-*.json"))
            self.assertEqual(len(backups), 2)  # 初回は上書き対象が無いので生成されない
            first = json.loads(backups[0].read_text(encoding="utf-8"))
            self.assertEqual(first["personas"][0]["variants"]["ja-JP"]["overview"], "版0")

    def test_generations_are_capped(self):
        with tempfile.TemporaryDirectory() as t:
            store = LibraryStore(AppConfig(data_dir=Path(t)))
            for i in range(BACKUP_GENERATIONS + 5):
                store.save(self._doc(f"版{i}"))
            self.assertEqual(
                len(list(store.backups_dir().glob("library-*.json"))), BACKUP_GENERATIONS
            )

    def test_the_newest_backup_is_the_version_before_the_last_save(self):
        with tempfile.TemporaryDirectory() as t:
            store = LibraryStore(AppConfig(data_dir=Path(t)))
            store.save(self._doc("古い"))
            store.save(self._doc("新しい"))
            newest = sorted(store.backups_dir().glob("library-*.json"))[-1]
            raw = json.loads(newest.read_text(encoding="utf-8"))
            self.assertEqual(raw["personas"][0]["variants"]["ja-JP"]["overview"], "古い")
            self.assertEqual(store.load().persona("ppr-1").variants["ja-JP"].overview, "新しい")


class ConcurrentWriteProtection(unittest.TestCase):
    """古いプロセスが残っていて、新しい内容を潰す事故を防ぐ。"""

    def _doc(self, text: str) -> Library:
        return Library().with_persona(
            PersonaRecord("ppr-1", {"ja-JP": PersonaDocument(
                persona_id="ppr-1", locale="ja-JP", name="テスト", overview=text)})
        )

    def test_save_is_refused_after_another_process_wrote(self):
        from ppr.storage import LibraryConflictError

        with tempfile.TemporaryDirectory() as t:
            config = AppConfig(data_dir=Path(t))
            old = LibraryStore(config)
            old.save(self._doc("初版"))
            old.load()                       # 古いプロセスが読み込む

            new = LibraryStore(config)       # 別プロセス相当
            new.load()
            new.save(self._doc("新しい内容"))

            with self.assertRaises(LibraryConflictError):
                old.save(self._doc("古いプロセスの上書き"))
            self.assertEqual(
                LibraryStore(config).load().persona("ppr-1").variants["ja-JP"].overview,
                "新しい内容",
            )

    def test_force_allows_the_overwrite(self):
        with tempfile.TemporaryDirectory() as t:
            config = AppConfig(data_dir=Path(t))
            old = LibraryStore(config)
            old.save(self._doc("初版"))
            old.load()
            other = LibraryStore(config)
            other.load()
            other.save(self._doc("新しい内容"))
            old.save(self._doc("強制上書き"), force=True)
            self.assertEqual(
                LibraryStore(config).load().persona("ppr-1").variants["ja-JP"].overview,
                "強制上書き",
            )

    def test_consecutive_saves_from_the_same_store_are_allowed(self):
        with tempfile.TemporaryDirectory() as t:
            store = LibraryStore(AppConfig(data_dir=Path(t)))
            store.load()
            store.save(self._doc("一"))
            store.save(self._doc("二"))
            store.save(self._doc("三"))
            self.assertEqual(store.load().persona("ppr-1").variants["ja-JP"].overview, "三")

    def test_first_save_into_an_empty_directory_is_allowed(self):
        with tempfile.TemporaryDirectory() as t:
            store = LibraryStore(AppConfig(data_dir=Path(t)))
            store.load()
            store.save(self._doc("初版"))
            self.assertEqual(store.load().persona("ppr-1").variants["ja-JP"].overview, "初版")


class LockOwnership(unittest.TestCase):
    def test_held_is_true_while_acquired(self):
        from ppr.storage import LibraryLock

        with tempfile.TemporaryDirectory() as t:
            lock = LibraryLock(AppConfig(data_dir=Path(t)))
            self.assertFalse(lock.held())
            with lock:
                self.assertTrue(lock.held())
            self.assertFalse(lock.held())

    def test_held_is_false_when_the_lock_file_was_taken_over(self):
        from ppr.storage import LibraryLock

        with tempfile.TemporaryDirectory() as t:
            config = AppConfig(data_dir=Path(t))
            lock = LibraryLock(config)
            with lock:
                config.lock_path.write_text("999999", encoding="ascii")
                self.assertFalse(lock.held())
