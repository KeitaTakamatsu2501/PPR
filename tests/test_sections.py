import unittest

from ppr.sections import (
    heading_outline,
    join_sections,
    replace_section_text,
    split_sections,
)

SAMPLE = """前書きの一文。

【人格の核】
核の本文。
複数行ある。

【口調の基本】
口調の本文。  
末尾に空白がある。
"""


class SplitAndJoin(unittest.TestCase):
    def test_round_trip_is_exact(self):
        for text in (SAMPLE, "", "見出しなしの本文", "【のみ】", "\n\n\n", "【a】\n本文  \n\n"):
            self.assertEqual(join_sections(split_sections(text)), text, repr(text))

    def test_preamble_is_kept_as_its_own_section(self):
        sections = split_sections(SAMPLE)
        self.assertTrue(sections[0].is_preamble)
        self.assertEqual(sections[0].text, "前書きの一文。\n\n")

    def test_headings_are_found_in_order(self):
        self.assertEqual(heading_outline(SAMPLE), ("人格の核", "口調の基本"))

    def test_empty_text_yields_one_empty_section(self):
        sections = split_sections("")
        self.assertEqual(len(sections), 1)
        self.assertEqual(sections[0].text, "")

    def test_text_without_headings_is_a_single_section(self):
        sections = split_sections("ただの本文")
        self.assertEqual(len(sections), 1)
        self.assertTrue(sections[0].is_preamble)

    def test_bracket_in_the_middle_of_a_line_is_not_a_heading(self):
        text = "これは【見出しではない】文中の括弧です。\n"
        self.assertEqual(heading_outline(text), ())

    def test_trailing_text_after_a_heading_on_the_same_line_is_not_a_heading(self):
        text = "【見出し】直後に本文\n"
        self.assertEqual(heading_outline(text), ())

    def test_body_excludes_the_heading_line(self):
        section = split_sections(SAMPLE)[1]
        self.assertEqual(section.heading, "人格の核")
        self.assertTrue(section.body.startswith("核の本文。"))
        self.assertNotIn("【人格の核】", section.body)

    def test_trailing_whitespace_inside_a_section_survives(self):
        sections = split_sections(SAMPLE)
        self.assertIn("口調の本文。  \n", sections[-1].text)


class Replace(unittest.TestCase):
    def test_other_sections_are_untouched(self):
        updated = replace_section_text(SAMPLE, 1, "【人格の核】\n置き換えた本文。\n")
        self.assertIn("置き換えた本文。", updated)
        self.assertIn("前書きの一文。", updated)
        self.assertIn("口調の本文。  \n", updated)

    def test_replacing_with_identical_text_is_a_no_op(self):
        sections = split_sections(SAMPLE)
        self.assertEqual(replace_section_text(SAMPLE, 1, sections[1].text), SAMPLE)

    def test_out_of_range_is_refused(self):
        with self.assertRaises(IndexError):
            replace_section_text(SAMPLE, 99, "x")


if __name__ == "__main__":
    unittest.main()
