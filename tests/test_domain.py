import unittest

from ppr.domain import (
    VALUE_BOUNDARY_CATEGORY,
    Library,
    PersonaDocument,
    PersonaError,
    PersonaRecord,
    PersonStance,
    TopicStance,
)


def make_document(**overrides) -> PersonaDocument:
    base = dict(
        persona_id="ppr-test",
        locale="ja-JP",
        name="テスト",
        overview="概要",
        topic_stances=(
            TopicStance("e-1", "人類", "大分類の本文"),
            TopicStance("e-2", "人類/人間の本性", "小カテゴリの本文"),
            TopicStance("e-3", VALUE_BOUNDARY_CATEGORY, "【恒常的な忌避価値】…"),
        ),
        person_stances=(
            PersonStance("e-p1", "相手A", "関係の本文", "target-a", "Aさん", "included"),
            PersonStance("e-p2", "名前だけの旧関係", "本文", "", "", ""),
        ),
    )
    base.update(overrides)
    return PersonaDocument(**base)


class TextPreservation(unittest.TestCase):
    def test_leading_and_trailing_whitespace_survives_round_trip(self):
        raw = "  先頭に空白\n途中に改行\n\n末尾に空白と改行  \n"
        doc = make_document(basic_settings=raw)
        restored = PersonaDocument.from_json(doc.to_json())
        self.assertEqual(restored.basic_settings, raw)

    def test_non_ascii_and_long_text_survive(self):
        raw = "日本語＋絵文字🌱＋한국어＋中文" + "あ" * 5000
        doc = make_document(speaking_style=raw)
        restored = PersonaDocument.from_json(doc.to_json())
        self.assertEqual(restored.speaking_style, raw)

    def test_array_order_is_preserved(self):
        doc = make_document()
        restored = PersonaDocument.from_json(doc.to_json())
        self.assertEqual(
            [t.category for t in restored.topic_stances],
            ["人類", "人類/人間の本性", VALUE_BOUNDARY_CATEGORY],
        )

    def test_non_string_body_is_rejected(self):
        with self.assertRaises(PersonaError):
            PersonaDocument.from_json({"persona_id": "x", "locale": "ja-JP", "name": 123})


class CategoryClassification(unittest.TestCase):
    def test_value_boundary_is_found_and_is_not_a_small_category(self):
        doc = make_document()
        boundary = doc.value_boundary
        self.assertIsNotNone(boundary)
        self.assertFalse(boundary.is_small_category)

    def test_small_categories_exclude_large_categories_and_boundary(self):
        doc = make_document()
        self.assertEqual([t.category for t in doc.small_category_stances], ["人類/人間の本性"])

    def test_large_categories_exclude_the_value_boundary(self):
        self.assertEqual(make_document().large_categories, ("人類",))

    def test_blank_stance_is_not_offered_as_a_candidate(self):
        doc = make_document(
            topic_stances=(TopicStance("e-1", "人類/人間の本性", "   "),)
        )
        self.assertEqual(doc.small_category_stances, ())


class PersonStanceResolution(unittest.TestCase):
    def test_only_the_current_partner_is_returned(self):
        doc = make_document()
        self.assertEqual(doc.person_stance_for("target-a").name, "相手A")

    def test_name_only_rows_are_never_matched_by_guessing(self):
        doc = make_document()
        self.assertIsNone(doc.person_stance_for("unknown-target"))

    def test_same_name_different_id_is_not_confused(self):
        doc = make_document(
            person_stances=(
                PersonStance("e-1", "同名", "こちらが正", "id-1"),
                PersonStance("e-2", "同名", "こちらは別人", "id-2"),
            )
        )
        self.assertEqual(doc.person_stance_for("id-2").stance, "こちらは別人")

    def test_stance_empty_but_address_override_present_is_kept(self):
        doc = make_document(
            person_stances=(PersonStance("e-1", "相手", "", "id-1", "お前", "excluded"),)
        )
        restored = PersonaDocument.from_json(doc.to_json())
        kept = restored.person_stance_for("id-1")
        self.assertEqual(kept.direct_address_override, "お前")
        self.assertEqual(kept.perceived_humanity_override, "excluded")


class Validation(unittest.TestCase):
    def test_incomplete_persona_is_still_storable(self):
        doc = PersonaDocument(persona_id="ppr-x", locale="ja-JP", name="")
        self.assertEqual(doc.validation_issues(), ())

    def test_rehearsal_requires_name_and_a_body(self):
        doc = PersonaDocument(persona_id="ppr-x", locale="ja-JP", name="")
        issues = doc.validation_issues(for_rehearsal=True)
        self.assertEqual(len(issues), 2)

    def test_duplicate_entry_ids_are_reported(self):
        doc = make_document(
            topic_stances=(TopicStance("dup", "a", "1"), TopicStance("dup", "b", "2"))
        )
        self.assertTrue(any("重複" in i for i in doc.validation_issues()))


class LibraryRoundTrip(unittest.TestCase):
    def test_identity_survives_a_name_change(self):
        record = PersonaRecord("ppr-1", {"ja-JP": make_document(persona_id="ppr-1")})
        library = Library().with_persona(record)
        doc = library.persona("ppr-1").variants["ja-JP"]
        renamed = record.__class__("ppr-1", {"ja-JP": doc.with_changes(name="別名")})
        library = library.with_persona(renamed)
        self.assertEqual(len(library.personas), 1)
        self.assertEqual(library.persona("ppr-1").variants["ja-JP"].name, "別名")

    def test_editing_one_locale_does_not_touch_another(self):
        record = PersonaRecord(
            "ppr-1",
            {
                "ja-JP": make_document(persona_id="ppr-1", overview="日本語"),
                "en-US": make_document(persona_id="ppr-1", locale="en-US", overview="English"),
            },
        )
        library = Library().with_persona(record)
        updated = dict(library.persona("ppr-1").variants)
        updated["ja-JP"] = updated["ja-JP"].with_changes(overview="書き換え")
        library = library.with_persona(PersonaRecord("ppr-1", updated))
        self.assertEqual(library.persona("ppr-1").variants["en-US"].overview, "English")

    def test_unknown_schema_version_is_refused(self):
        with self.assertRaises(PersonaError):
            Library.from_json({"schema_version": 99, "personas": []})


if __name__ == "__main__":
    unittest.main()
