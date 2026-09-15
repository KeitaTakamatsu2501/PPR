import unittest

from ppr.domain import VALUE_BOUNDARY_CATEGORY, Library, PersonaDocument, PersonaRecord, PersonStance, TopicStance
from ppr.ui.state import EditorState

BODY = """前書き。

【人格の核】
核の本文。  

【口調の基本】
口調の本文。
"""


def library() -> Library:
    doc = PersonaDocument(
        persona_id="ppr-1",
        locale="ja-JP",
        name="テスト",
        basic_settings=BODY,
        topic_stances=(
            TopicStance("t-1", "人類", "大分類"),
            TopicStance("t-2", "人類/人間の本性", "小カテゴリ"),
            TopicStance("t-3", "技術/交通", "別の大分類"),
            TopicStance("t-4", VALUE_BOUNDARY_CATEGORY, "【恒常的な忌避価値】…"),
        ),
        person_stances=(
            PersonStance("p-1", "既知", "本文", "ppr-2", "既知さん", "included"),
            PersonStance("p-2", "未知", "本文", "gone-9"),
        ),
    )
    other = PersonaDocument(persona_id="ppr-2", locale="ja-JP", name="相手")
    en = PersonaDocument(persona_id="ppr-1", locale="en-US", name="Test", basic_settings="English")
    return (
        Library()
        .with_persona(PersonaRecord("ppr-1", {"ja-JP": doc, "en-US": en}))
        .with_persona(PersonaRecord("ppr-2", {"ja-JP": other}))
    )


class Dirty(unittest.TestCase):
    def test_a_freshly_opened_state_is_clean(self):
        self.assertFalse(EditorState.open(library(), "ppr-1", "ja-JP").dirty)

    def test_editing_marks_it_dirty(self):
        state = EditorState.open(library(), "ppr-1", "ja-JP")
        state.set_basic("name", "変更後")
        self.assertTrue(state.dirty)

    def test_reverting_the_text_makes_it_clean_again(self):
        state = EditorState.open(library(), "ppr-1", "ja-JP")
        state.set_basic("name", "変更後")
        state.set_basic("name", "テスト")
        self.assertFalse(state.dirty)

    def test_commit_resets_the_baseline(self):
        state = EditorState.open(library(), "ppr-1", "ja-JP")
        state.set_basic("name", "変更後")
        state.commit()
        self.assertFalse(state.dirty)


class BodyEditing(unittest.TestCase):
    def test_sections_are_listed(self):
        state = EditorState.open(library(), "ppr-1", "ja-JP")
        sections = state.sections("basic_settings")
        self.assertEqual([s.heading for s in sections], ["", "人格の核", "口調の基本"])

    def test_editing_one_section_leaves_the_others_byte_identical(self):
        state = EditorState.open(library(), "ppr-1", "ja-JP")
        state.set_section("basic_settings", 1, "【人格の核】\n書き換えた。\n\n")
        body = state.body("basic_settings")
        self.assertIn("書き換えた。", body)
        self.assertTrue(body.startswith("前書き。\n\n"))
        self.assertIn("【口調の基本】\n口調の本文。\n", body)

    def test_trailing_whitespace_in_an_untouched_section_survives(self):
        state = EditorState.open(library(), "ppr-1", "ja-JP")
        state.set_section("basic_settings", 2, "【口調の基本】\nx\n")
        self.assertIn("核の本文。  \n", state.body("basic_settings"))

    def test_unknown_body_field_is_refused(self):
        state = EditorState.open(library(), "ppr-1", "ja-JP")
        with self.assertRaises(KeyError):
            state.set_body("voice_profile_id", "x")


class TopicEditing(unittest.TestCase):
    def test_value_boundary_is_grouped_first(self):
        state = EditorState.open(library(), "ppr-1", "ja-JP")
        groups = state.grouped_topics()
        self.assertEqual(groups[0][0], "恒常的な価値境界")
        self.assertEqual([g[0] for g in groups[1:]], ["人類", "技術"])

    def test_editing_a_stance_keeps_order_and_category(self):
        state = EditorState.open(library(), "ppr-1", "ja-JP")
        state.set_topic_stance("t-2", "書き換えた")
        rows = state.document.topic_stances
        self.assertEqual([r.entry_id for r in rows], ["t-1", "t-2", "t-3", "t-4"])
        self.assertEqual(rows[1].stance, "書き換えた")
        self.assertEqual(rows[1].category, "人類/人間の本性")

    def test_renaming_a_category_keeps_the_stance_text(self):
        state = EditorState.open(library(), "ppr-1", "ja-JP")
        state.rename_topic_category("t-2", "人類/新しい名前")
        row = next(r for r in state.document.topic_stances if r.entry_id == "t-2")
        self.assertEqual(row.category, "人類/新しい名前")
        self.assertEqual(row.stance, "小カテゴリ")

    def test_moving_a_row_changes_order_only(self):
        state = EditorState.open(library(), "ppr-1", "ja-JP")
        state.move_topic_stance("t-3", -1)
        self.assertEqual([r.entry_id for r in state.document.topic_stances], ["t-1", "t-3", "t-2", "t-4"])

    def test_removing_returns_the_row_so_the_caller_can_confirm(self):
        state = EditorState.open(library(), "ppr-1", "ja-JP")
        removed = state.remove_topic_stance("t-2")
        self.assertEqual(removed.stance, "小カテゴリ")
        self.assertEqual(len(state.document.topic_stances), 3)

    def test_adding_a_row_gets_a_fresh_entry_id(self):
        state = EditorState.open(library(), "ppr-1", "ja-JP")
        entry_id = state.add_topic_stance("新規/カテゴリ")
        self.assertNotIn(entry_id, {"t-1", "t-2", "t-3", "t-4"})


class PersonEditing(unittest.TestCase):
    def test_unresolved_targets_are_reported_but_kept(self):
        state = EditorState.open(library(), "ppr-1", "ja-JP")
        unresolved = state.unresolved_person_targets()
        self.assertEqual([u.target_character_id for u in unresolved], ["gone-9"])
        self.assertEqual(len(state.document.person_stances), 2)

    def test_address_override_can_be_edited_without_touching_the_stance(self):
        state = EditorState.open(library(), "ppr-1", "ja-JP")
        state.set_person_stance("p-1", direct_address_override="呼び方を変えた")
        row = state.document.person_stance_for("ppr-2")
        self.assertEqual(row.direct_address_override, "呼び方を変えた")
        self.assertEqual(row.stance, "本文")

    def test_unknown_field_is_refused(self):
        state = EditorState.open(library(), "ppr-1", "ja-JP")
        with self.assertRaises(KeyError):
            state.set_person_stance("p-1", voice_profile_id="x")


class Isolation(unittest.TestCase):
    def test_commit_does_not_touch_the_other_locale(self):
        state = EditorState.open(library(), "ppr-1", "ja-JP")
        state.set_basic("name", "日本語だけ変更")
        updated = state.commit()
        self.assertEqual(updated.persona("ppr-1").variants["en-US"].name, "Test")
        self.assertEqual(updated.persona("ppr-1").variants["en-US"].basic_settings, "English")

    def test_commit_does_not_touch_other_personas(self):
        state = EditorState.open(library(), "ppr-1", "ja-JP")
        state.set_basic("name", "変更")
        updated = state.commit()
        self.assertEqual(updated.persona("ppr-2").variants["ja-JP"].name, "相手")

    def test_persona_id_survives_a_rename(self):
        state = EditorState.open(library(), "ppr-1", "ja-JP")
        state.set_basic("name", "全然ちがう名前")
        updated = state.commit()
        self.assertIsNotNone(updated.persona("ppr-1"))
        self.assertEqual(len(updated.personas), 2)


if __name__ == "__main__":
    unittest.main()


class TargetBoundWrites(unittest.TestCase):
    """GUI再描画中の誤書き込みを防ぐ束縛の検証。

    これが機能しないと、人格を切り替えたときに直前の人格の本文が
    新しい人格へ書き込まれる（実際に起きた不具合）。
    """

    def _states(self):
        lib = library()
        return EditorState.open(lib, "ppr-1", "ja-JP"), EditorState.open(lib, "ppr-2", "ja-JP")

    def test_section_write_from_another_persona_is_ignored(self):
        first, second = self._states()
        stale = first.target_for_section("basic_settings", 1)
        before = second.document.basic_settings
        self.assertFalse(second.apply_section(stale, "【人格の核】\n他人の本文\n"))
        self.assertEqual(second.document.basic_settings, before)
        self.assertFalse(second.dirty)

    def test_section_write_from_another_locale_is_ignored(self):
        lib = library()
        ja = EditorState.open(lib, "ppr-1", "ja-JP")
        en = EditorState.open(lib, "ppr-1", "en-US")
        stale = ja.target_for_section("basic_settings", 1)
        self.assertFalse(en.apply_section(stale, "日本語の本文"))
        self.assertEqual(en.document.basic_settings, "English")

    def test_matching_target_writes(self):
        state, _ = self._states()
        target = state.target_for_section("basic_settings", 1)
        self.assertTrue(state.apply_section(target, "【人格の核】\n書き換えた。\n\n"))
        self.assertIn("書き換えた。", state.body("basic_settings"))

    def test_identical_value_is_not_a_change(self):
        state, _ = self._states()
        target = state.target_for_section("basic_settings", 1)
        current = state.sections("basic_settings")[1].text
        self.assertFalse(state.apply_section(target, current))
        self.assertFalse(state.dirty)

    def test_out_of_range_index_is_ignored(self):
        state, _ = self._states()
        self.assertFalse(state.apply_section(state.target_for_section("basic_settings", 99), "x"))
        self.assertFalse(state.dirty)

    def test_wrong_body_field_after_switching_is_ignored(self):
        """本文の種類を切り替えた直後、古い区画番号で新しい本文を潰さない。"""
        state, _ = self._states()
        # overview は1区画しかない。basic_settings の区画2を指す束縛は適用されない。
        self.assertFalse(state.apply_section(state.target_for_section("overview", 2), "壊す"))
        self.assertEqual(state.document.overview, "")

    def test_topic_write_from_another_persona_is_ignored(self):
        first, second = self._states()
        self.assertFalse(second.apply_topic(first.target_for_topic("t-2"), "他人の価値観"))
        self.assertFalse(second.dirty)

    def test_person_write_from_another_persona_is_ignored(self):
        first, second = self._states()
        stale = first.target_for_person("p-1")
        self.assertFalse(second.apply_person(stale, {"stance": "他人の関係"}))
        self.assertFalse(second.dirty)

    def test_none_target_is_ignored(self):
        state, _ = self._states()
        self.assertFalse(state.apply_section(None, "x"))
        self.assertFalse(state.apply_topic(None, "x"))
        self.assertFalse(state.apply_person(None, {"stance": "x"}))
        self.assertFalse(state.dirty)

    def test_unknown_entry_id_is_ignored(self):
        state, _ = self._states()
        self.assertFalse(state.apply_topic(state.target_for_topic("消えた行"), "x"))
        self.assertFalse(state.dirty)
