import json
import tempfile
import unittest
from pathlib import Path

from ppr.adapters.jts.importer import (
    JtsImportError,
    import_locale,
    locale_paths,
    sha256_of,
    source_drift,
)

TRICKY_BODY = "  先頭空白\n中の改行\n\n末尾  \n"
TRICKY_SAMPLES = "「台詞」\t タブと絵文字🌱 " + "長" * 800


def write_pair(root: Path, locale: str = "ja-JP", *, fixed=None, detail=None) -> None:
    pair = locale_paths(root, locale)
    pair.fixed_path.parent.mkdir(parents=True, exist_ok=True)
    pair.fixed_path.write_text(json.dumps(fixed, ensure_ascii=False, indent=2), encoding="utf-8")
    pair.detail_path.write_text(json.dumps(detail, ensure_ascii=False, indent=2), encoding="utf-8")


def default_fixed():
    return {
        "schema_version": 8,
        "characters": [
            {
                "id": "aaa-1",
                "name": "あるふぁ",
                "enabled": False,
                "first_person": "私",
                "second_person": "あなた",
                "species_label": "宇宙人",
                "humanity_membership": "excluded",
                "voice_profile_id": "v2ProPlus:Alpha",
                "reference_audio": "/absolute/path/alpha.wav",
                "reference_text": "参照テキスト",
                "voice_volume_percent": 80,
                "reference_sets": {"normal": [{"audio": "/absolute/path/alpha.wav", "text": "x"}]},
                "default_personality_id": "p-1",
                "personalities": [{"id": "p-1", "name": "既定", "description": "d"}],
                "default_one_to_one_personality_id": "o-1",
                "one_to_one_personalities": [{"id": "o-1", "name": "一対一", "prompt": "p"}],
                "未知の固定フィールド": {"保持されるべき": True},
            },
            {"id": "bbb-2", "name": "べーた", "humanity_membership": "included"},
        ],
    }


def default_detail():
    return {
        "schema_version": 2,
        "characters": [
            {
                "character_id": "aaa-1",
                "overview": "概要",
                "basic_settings": TRICKY_BODY,
                "speaking_style": "",
                "dialogue_samples": TRICKY_SAMPLES,
                "topic_stances": [
                    {"category": "人類", "stance": "大分類の本文"},
                    {"category": "人類/人間の本性", "stance": "小カテゴリの本文"},
                    {"category": "恒常的な価値境界", "stance": "【恒常的な忌避価値】…"},
                    {"category": "人類/人間の本性", "stance": "同名カテゴリの二行目"},
                ],
                "person_stances": [
                    {"name": "べーた", "stance": "関係の本文", "target_character_id": "bbb-2",
                     "direct_address_override": "べーたさん", "perceived_humanity_override": "included"},
                    {"name": "消えた相手", "stance": "本文", "target_character_id": "deleted-9"},
                    {"name": "名前だけの旧関係", "stance": "本文"},
                    {"name": "呼称だけ", "stance": "", "target_character_id": "ccc-3",
                     "direct_address_override": "お前", "perceived_humanity_override": "excluded"},
                ],
                "未知の詳細フィールド": [1, 2, 3],
            },
            {"character_id": "bbb-2", "overview": "ベータの概要"},
        ],
    }


class Preservation(unittest.TestCase):
    def _import(self, tmp: Path, ids=("aaa-1",)):
        root = tmp / "jts"
        write_pair(root, fixed=default_fixed(), detail=default_detail())
        return root, import_locale(root, "ja-JP", persona_ids=ids, imports_dir=tmp / "imports")

    def test_bodies_are_preserved_byte_for_byte(self):
        with tempfile.TemporaryDirectory() as t:
            _, result = self._import(Path(t))
            doc = result.records[0].variants["ja-JP"]
            self.assertEqual(doc.basic_settings, TRICKY_BODY)
            self.assertEqual(doc.dialogue_samples, TRICKY_SAMPLES)
            self.assertEqual(doc.speaking_style, "")

    def test_same_name_categories_are_kept_as_separate_rows(self):
        with tempfile.TemporaryDirectory() as t:
            _, result = self._import(Path(t))
            doc = result.records[0].variants["ja-JP"]
            same = [x for x in doc.topic_stances if x.category == "人類/人間の本性"]
            self.assertEqual(len(same), 2)
            self.assertNotEqual(same[0].entry_id, same[1].entry_id)
            self.assertEqual(same[1].stance, "同名カテゴリの二行目")

    def test_person_stance_variants_are_all_kept(self):
        with tempfile.TemporaryDirectory() as t:
            _, result = self._import(Path(t))
            doc = result.records[0].variants["ja-JP"]
            self.assertEqual(len(doc.person_stances), 4)
            self.assertEqual(doc.person_stance_for("deleted-9").name, "消えた相手")
            self.assertIsNone(doc.person_stance_for(""))
            only_address = doc.person_stance_for("ccc-3")
            self.assertEqual(only_address.stance, "")
            self.assertEqual(only_address.direct_address_override, "お前")

    def test_fixed_persona_fields_are_taken_but_voice_is_not(self):
        with tempfile.TemporaryDirectory() as t:
            _, result = self._import(Path(t))
            doc = result.records[0].variants["ja-JP"]
            self.assertEqual(doc.species_label, "宇宙人")
            self.assertEqual(doc.humanity_membership, "excluded")
            payload = doc.to_json()
            for key in ("voice_profile_id", "reference_audio", "reference_sets", "personalities"):
                self.assertNotIn(key, payload)

    def test_entry_ids_are_deterministic_across_reimports(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t) / "jts"
            write_pair(root, fixed=default_fixed(), detail=default_detail())
            first = import_locale(root, "ja-JP", persona_ids=("aaa-1",), imports_dir=Path(t) / "i1")
            second = import_locale(root, "ja-JP", persona_ids=("aaa-1",), imports_dir=Path(t) / "i2")
            self.assertEqual(
                [x.entry_id for x in first.records[0].variants["ja-JP"].topic_stances],
                [x.entry_id for x in second.records[0].variants["ja-JP"].topic_stances],
            )

    def test_only_requested_personas_are_imported(self):
        with tempfile.TemporaryDirectory() as t:
            _, result = self._import(Path(t))
            self.assertEqual([r.persona_id for r in result.records], ["aaa-1"])

    def test_originals_are_copied_with_recorded_hashes(self):
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t)
            root, result = self._import(tmp)
            stored = tmp / "imports" / result.reference.import_id / "files"
            pair = locale_paths(root, "ja-JP")
            copied = stored / pair.fixed_relative
            self.assertTrue(copied.exists())
            self.assertEqual(sha256_of(copied), sha256_of(pair.fixed_path))
            # 未知フィールドは原本側に残っている
            raw = json.loads(copied.read_text(encoding="utf-8"))
            self.assertIn("未知の固定フィールド", raw["characters"][0])


class SourceIsNeverModified(unittest.TestCase):
    def test_import_does_not_change_the_source_files(self):
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t)
            root = tmp / "jts"
            write_pair(root, fixed=default_fixed(), detail=default_detail())
            pair = locale_paths(root, "ja-JP")
            before = (sha256_of(pair.fixed_path), sha256_of(pair.detail_path))
            import_locale(root, "ja-JP", persona_ids=("aaa-1",), imports_dir=tmp / "imports")
            after = (sha256_of(pair.fixed_path), sha256_of(pair.detail_path))
            self.assertEqual(before, after)

    def test_drift_is_reported_after_the_source_changes(self):
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t)
            root = tmp / "jts"
            write_pair(root, fixed=default_fixed(), detail=default_detail())
            result = import_locale(root, "ja-JP", persona_ids=("aaa-1",), imports_dir=tmp / "imports")
            self.assertEqual(source_drift(result.reference, root), ())
            detail = default_detail()
            detail["characters"][0]["overview"] = "JTS側で書き換えられた"
            write_pair(root, fixed=default_fixed(), detail=detail)
            drift = source_drift(result.reference, root)
            self.assertEqual(len(drift), 1)
            self.assertIn("characters.json", drift[0])


class RefusedInputs(unittest.TestCase):
    def _expect_refusal(self, *, fixed=None, detail=None, ids=("aaa-1",), only_fixed=False):
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t)
            root = tmp / "jts"
            pair = locale_paths(root, "ja-JP")
            pair.fixed_path.parent.mkdir(parents=True, exist_ok=True)
            pair.fixed_path.write_text(
                json.dumps(fixed if fixed is not None else default_fixed(), ensure_ascii=False),
                encoding="utf-8",
            )
            if not only_fixed:
                pair.detail_path.write_text(
                    json.dumps(detail if detail is not None else default_detail(), ensure_ascii=False),
                    encoding="utf-8",
                )
            with self.assertRaises(JtsImportError):
                import_locale(root, "ja-JP", persona_ids=ids, imports_dir=tmp / "imports")

    def test_missing_counterpart_is_refused(self):
        self._expect_refusal(only_fixed=True)

    def test_unknown_fixed_schema_version_is_refused(self):
        bad = default_fixed()
        bad["schema_version"] = 9
        self._expect_refusal(fixed=bad)

    def test_unknown_detail_schema_version_is_refused(self):
        bad = default_detail()
        bad["schema_version"] = 3
        self._expect_refusal(detail=bad)

    def test_detail_without_a_fixed_row_is_refused(self):
        bad = default_detail()
        bad["characters"].append({"character_id": "orphan-9", "overview": "孤児"})
        self._expect_refusal(detail=bad)

    def test_duplicate_ids_are_refused(self):
        bad = default_fixed()
        bad["characters"].append({"id": "aaa-1", "name": "重複"})
        self._expect_refusal(fixed=bad)

    def test_duplicate_json_keys_are_refused(self):
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t)
            root = tmp / "jts"
            pair = locale_paths(root, "ja-JP")
            pair.fixed_path.parent.mkdir(parents=True, exist_ok=True)
            pair.fixed_path.write_text(
                '{"schema_version": 8, "schema_version": 8, "characters": []}', encoding="utf-8"
            )
            pair.detail_path.write_text(json.dumps(default_detail(), ensure_ascii=False), encoding="utf-8")
            with self.assertRaises(JtsImportError):
                import_locale(root, "ja-JP", persona_ids=("aaa-1",), imports_dir=tmp / "imports")

    def test_missing_requested_persona_is_reported_not_crashed(self):
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t)
            root = tmp / "jts"
            write_pair(root, fixed=default_fixed(), detail=default_detail())
            result = import_locale(
                root, "ja-JP", persona_ids=("aaa-1", "zzz-9"), imports_dir=tmp / "imports"
            )
            self.assertEqual(result.skipped_ids, ["zzz-9"])
            self.assertEqual(len(result.records), 1)


if __name__ == "__main__":
    unittest.main()
