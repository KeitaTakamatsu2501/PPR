import json
import os
import tempfile
import unittest
from pathlib import Path

from ppr.config import AppConfig
from ppr.domain import Library, PersonaDocument, PersonaRecord, PersonStance, TopicStance
from ppr.revisions import RevisionStore, canonical_json, revision_id_for
from ppr.storage import LibraryLock, LibraryLockedError, LibraryStore


def doc(**overrides) -> PersonaDocument:
    base = dict(persona_id="ppr-1", locale="ja-JP", name="テスト", overview="概要")
    base.update(overrides)
    return PersonaDocument(**base)


class Revisions(unittest.TestCase):
    def test_same_content_gives_the_same_revision(self):
        self.assertEqual(revision_id_for(doc()), revision_id_for(doc()))

    def test_changed_body_gives_a_different_revision(self):
        self.assertNotEqual(revision_id_for(doc()), revision_id_for(doc(overview="別")))

    def test_array_order_changes_the_revision(self):
        a = doc(topic_stances=(TopicStance("e1", "A", "1"), TopicStance("e2", "B", "2")))
        b = doc(topic_stances=(TopicStance("e2", "B", "2"), TopicStance("e1", "A", "1")))
        self.assertNotEqual(revision_id_for(a), revision_id_for(b))

    def test_trailing_whitespace_changes_the_revision(self):
        self.assertNotEqual(revision_id_for(doc(overview="概要")), revision_id_for(doc(overview="概要 ")))

    def test_canonical_json_refuses_nan(self):
        with self.assertRaises(ValueError):
            canonical_json({"x": float("nan")})

    def test_store_round_trip_and_reuse(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = RevisionStore(Path(tmp))
            first = store.create(doc())
            second = store.create(doc())
            self.assertEqual(first.revision_id, second.revision_id)
            loaded = store.load(first.revision_id)
            self.assertEqual(loaded.document, doc())

    def test_tampered_revision_file_is_detected(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = RevisionStore(Path(tmp))
            created = store.create(doc())
            path = store.path_for(created.revision_id)
            raw = json.loads(path.read_text(encoding="utf-8"))
            raw["overview"] = "改竄"
            path.write_text(canonical_json(raw), encoding="utf-8")
            with self.assertRaises(ValueError):
                store.load(created.revision_id)


class Storage(unittest.TestCase):
    def _config(self, tmp: str) -> AppConfig:
        return AppConfig(data_dir=Path(tmp))

    def test_save_and_load_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = self._config(tmp)
            store = LibraryStore(config)
            library = Library().with_persona(
                PersonaRecord("ppr-1", {"ja-JP": doc(
                    basic_settings="  末尾空白と改行  \n",
                    person_stances=(PersonStance("e1", "相手", "", "id-1", "お前", "excluded"),),
                )})
            )
            store.save(library)
            restored = store.load()
            got = restored.persona("ppr-1").variants["ja-JP"]
            self.assertEqual(got.basic_settings, "  末尾空白と改行  \n")
            self.assertEqual(got.person_stances[0].direct_address_override, "お前")

    def test_previous_version_is_kept_on_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = self._config(tmp)
            store = LibraryStore(config)
            store.save(Library().with_persona(PersonaRecord("ppr-1", {"ja-JP": doc(overview="一版目")})))
            store.save(Library().with_persona(PersonaRecord("ppr-1", {"ja-JP": doc(overview="二版目")})))
            previous = json.loads((config.data_dir / "library.prev.json").read_text(encoding="utf-8"))
            self.assertEqual(previous["personas"][0]["variants"]["ja-JP"]["overview"], "一版目")
            self.assertEqual(store.load().persona("ppr-1").variants["ja-JP"].overview, "二版目")

    def test_no_temp_file_is_left_behind(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = self._config(tmp)
            LibraryStore(config).save(Library())
            self.assertEqual(list(config.data_dir.glob("*.tmp")), [])

    def test_missing_library_loads_as_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(LibraryStore(self._config(tmp)).load().personas, ())


class Locking(unittest.TestCase):
    def test_second_lock_is_refused_while_held(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = AppConfig(data_dir=Path(tmp))
            with LibraryLock(config):
                with self.assertRaises(LibraryLockedError):
                    LibraryLock(config).acquire()

    def test_lock_is_released_on_exit(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = AppConfig(data_dir=Path(tmp))
            with LibraryLock(config):
                pass
            with LibraryLock(config):
                pass

    def test_stale_lock_from_a_dead_process_is_reclaimed(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = AppConfig(data_dir=Path(tmp))
            config.lock_path.parent.mkdir(parents=True, exist_ok=True)
            # 存在しないPIDを書いた残骸
            dead = 2 ** 22
            while _pid_exists(dead):
                dead += 1
            config.lock_path.write_text(str(dead), encoding="ascii")
            with LibraryLock(config):
                pass


def _pid_exists(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        return True
    return True


if __name__ == "__main__":
    unittest.main()
