"""実データに対する検証。JTSが参照できない環境ではスキップする。

元JTSへは書き込まない。読み取りとハッシュ比較だけを行う。
"""

import json
import os
import tempfile
import unittest
from pathlib import Path

from ppr.adapters.jts.importer import import_locale, locale_paths, sha256_of
from ppr.assets import ASSET_PERSONA_IDS, ASSET_PERSONA_NAMES, JTS_ONLY_PERSONA_IDS
from ppr.config import APP_ROOT
from ppr.domain import VALUE_BOUNDARY_CATEGORY
from ppr.revisions import revision_id_for
from ppr.templates import load_bundled_template


def find_jts_root() -> Path | None:
    env = os.environ.get("PPR_TEST_JTS_ROOT", "").strip()
    candidates = [Path(env)] if env else []
    candidates.append(APP_ROOT.parent / "JTS")
    for candidate in candidates:
        if (candidate / "config" / "high_quality_generation" / "fixed_characters.json").exists():
            return candidate
    return None


JTS_ROOT = find_jts_root()


@unittest.skipIf(JTS_ROOT is None, "JTSが参照できないためスキップ")
class RealData(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        pair = locale_paths(JTS_ROOT, "ja-JP")
        cls.before = (sha256_of(pair.fixed_path), sha256_of(pair.detail_path))
        cls.result = import_locale(
            JTS_ROOT, "ja-JP",
            persona_ids=ASSET_PERSONA_IDS,
            imports_dir=Path(cls.tmp.name) / "imports",
        )
        cls.after = (sha256_of(pair.fixed_path), sha256_of(pair.detail_path))
        cls.raw_detail = {
            row["character_id"]: row
            for row in json.loads(pair.detail_path.read_text(encoding="utf-8"))["characters"]
        }
        cls.raw_fixed = {
            row["id"]: row
            for row in json.loads(pair.fixed_path.read_text(encoding="utf-8"))["characters"]
        }
        cls.docs = {r.persona_id: r.variants["ja-JP"] for r in cls.result.records}

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_source_files_are_untouched(self):
        self.assertEqual(self.before, self.after)

    def test_all_eight_assets_are_imported(self):
        self.assertEqual(sorted(self.docs), sorted(ASSET_PERSONA_IDS))
        self.assertEqual(self.result.skipped_ids, [])

    def test_names_match_the_expected_roster(self):
        for persona_id, expected in ASSET_PERSONA_NAMES.items():
            self.assertEqual(self.docs[persona_id].name, expected)

    def test_bodies_match_the_source_exactly(self):
        for persona_id, doc in self.docs.items():
            raw = self.raw_detail[persona_id]
            for field in ("overview", "basic_settings", "speaking_style", "dialogue_samples"):
                self.assertEqual(getattr(doc, field), raw[field], f"{persona_id}.{field}")

    def test_fixed_fields_match_the_source_exactly(self):
        for persona_id, doc in self.docs.items():
            raw = self.raw_fixed[persona_id]
            self.assertEqual(doc.first_person, raw.get("first_person", ""))
            self.assertEqual(doc.second_person, raw.get("second_person", ""))
            self.assertEqual(doc.species_label, raw.get("species_label", ""))
            self.assertEqual(doc.humanity_membership, raw.get("humanity_membership", ""))

    def test_every_asset_has_195_topic_stances(self):
        for persona_id, doc in self.docs.items():
            self.assertEqual(len(doc.topic_stances), 195, persona_id)

    def test_topic_stances_match_the_source_in_order_and_text(self):
        for persona_id, doc in self.docs.items():
            raw = self.raw_detail[persona_id]["topic_stances"]
            self.assertEqual([t.category for t in doc.topic_stances], [r["category"] for r in raw])
            self.assertEqual([t.stance for t in doc.topic_stances], [r["stance"] for r in raw])

    def test_every_asset_carries_the_value_boundary(self):
        for persona_id, doc in self.docs.items():
            boundary = doc.value_boundary
            self.assertIsNotNone(boundary, persona_id)
            self.assertEqual(boundary.category, VALUE_BOUNDARY_CATEGORY)
            self.assertIn("【恒常的な忌避価値】", boundary.stance)

    def test_small_category_count_matches_the_bundled_template(self):
        template = load_bundled_template()
        expected = sum(1 for c in template.categories if "/" in c)
        self.assertEqual(expected, 172)
        for persona_id, doc in self.docs.items():
            self.assertEqual(len(doc.small_category_stances), expected, persona_id)

    def test_large_categories_are_the_bundled_twenty_two(self):
        template = load_bundled_template()
        for persona_id, doc in self.docs.items():
            self.assertEqual(doc.large_categories, template.large_categories, persona_id)
            self.assertEqual(len(doc.large_categories), 22)

    def test_person_stances_are_eight_with_one_pointing_at_the_excluded_persona(self):
        excluded = JTS_ONLY_PERSONA_IDS[0]
        for persona_id, doc in self.docs.items():
            self.assertEqual(len(doc.person_stances), 8, persona_id)
            targets = {p.target_character_id for p in doc.person_stances}
            self.assertIn(excluded, targets, persona_id)
            self.assertNotIn(persona_id, targets, "自分自身への関係は存在しないはず")

    def test_unresolved_reference_to_the_excluded_persona_is_kept(self):
        excluded = JTS_ONLY_PERSONA_IDS[0]
        kept = [doc.person_stance_for(excluded) for doc in self.docs.values()]
        self.assertTrue(all(k is not None for k in kept))
        self.assertEqual(len(kept), 8)

    def test_address_overrides_match_the_source(self):
        for persona_id, doc in self.docs.items():
            raw = {r.get("target_character_id", ""): r for r in self.raw_detail[persona_id]["person_stances"]}
            for stance in doc.person_stances:
                source = raw[stance.target_character_id]
                self.assertEqual(
                    stance.direct_address_override, source.get("direct_address_override", "")
                )
                self.assertEqual(
                    stance.perceived_humanity_override,
                    source.get("perceived_humanity_override", ""),
                )

    def test_reimport_is_stable(self):
        second = import_locale(
            JTS_ROOT, "ja-JP",
            persona_ids=ASSET_PERSONA_IDS,
            imports_dir=Path(self.tmp.name) / "imports2",
        )
        for record in second.records:
            self.assertEqual(
                revision_id_for(record.variants["ja-JP"]),
                revision_id_for(self.docs[record.persona_id]),
                record.persona_id,
            )

    def test_body_sizes_match_phase_0_measurements(self):
        expected = {
            "hayamin-2548d9d189": 15368,
            "kikuko-de58d3efda": 12173,
            "eyja-9ca78ddb88": 8091,
            "muelsyse-dec1162707": 11378,
            "h-tsubasa-db3d1d5f64": 18716,
            "elmirea-ab93505eb7": 8276,
            "luciela-06fbc1148f": 13329,
            "ione-47f2d24ffb": 6056,
        }
        actual = {pid: doc.body_characters for pid, doc in self.docs.items()}
        self.assertEqual(actual, expected)


if __name__ == "__main__":
    unittest.main()
