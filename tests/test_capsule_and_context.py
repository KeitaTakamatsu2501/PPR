import unittest

from ppr.domain import VALUE_BOUNDARY_CATEGORY, PersonaDocument, PersonStance, TopicStance
from ppr.rehearsal.capsule import (
    COMPACT_MAX_CHARACTERS,
    DEFAULT_MAX_CHARACTERS,
    MODE_CAPSULE,
    MODE_FULL,
    build_capsule,
    build_persona_rendering,
    collect_fragments,
    persona_budget_for,
    render_full_persona,
)
from ppr.rehearsal.context import (
    MAX_SELECTED_CATEGORIES,
    build_persona_context,
    resolve_selected_categories,
    topic_category_candidates,
)

BOUNDARY = (
    "【恒常的な忌避価値】人を道具として扱う話を嫌う。"
    "【保護対象】切り捨てられた側。"
    "【反射的反論】誰が消されたかを問う。"
    "【モード別扱い】議論（attack）では一点に絞る。意見交換（object_and_expose_bias）では異議を置く。"
    "解説では事実を述べる。相談では相談者を優先する。"
    "【同意境界】人格に反する全面同意は禁止する。"
)


def speaker(**kw) -> PersonaDocument:
    base = dict(
        persona_id="ppr-a", locale="ja-JP", name="話者", first_person="私", second_person="あなた",
        species_label="人間", humanity_membership="included",
        overview="概要の本文。ここに人物の要点がある。",
        basic_settings="【基本属性】\n年齢: 不明\n\n【人格の核】\n核となる本文。\n",
        speaking_style="【声と基調】\n落ち着いた声。\n\n【禁止する話し方】\n高笑いはしない。\n",
        dialogue_samples="【初対面】\n「はじめまして。」\n",
        topic_stances=(
            TopicStance("t1", "人類", "大分類の本文"),
            TopicStance("t2", "人類/人間の本性", "本性についての本文"),
            TopicStance("t3", "社会/都市", "都市についての本文"),
            TopicStance("t4", "技術/交通", "交通についての本文"),
            TopicStance("t5", "言語/沈黙", "沈黙についての本文"),
            TopicStance("t6", "芸術/音楽", ""),
            TopicStance("t7", VALUE_BOUNDARY_CATEGORY, BOUNDARY),
        ),
        person_stances=(
            PersonStance("p1", "相手", "相手への関係の本文", "ppr-b", "相手さん", "excluded"),
        ),
    )
    base.update(kw)
    return PersonaDocument(**base)


def partner() -> PersonaDocument:
    return PersonaDocument(
        persona_id="ppr-b", locale="ja-JP", name="相手", species_label="女神",
        humanity_membership="excluded", overview="相手の秘密の来歴", basic_settings="相手の秘密の価値観",
    )


class CapsuleFidelity(unittest.TestCase):
    def test_every_fragment_appears_in_the_source(self):
        document = speaker()
        sources = "\n".join((
            document.overview, document.basic_settings,
            document.speaking_style, document.dialogue_samples,
        ))
        for fragment in collect_fragments(document, partner_character_id="ppr-b"):
            if fragment.heading in ("固定プロフィール", "人物ごとの感情・関係性"):
                continue
            self.assertIn(fragment.text, sources, fragment.text)

    def test_capsule_never_exceeds_the_budget(self):
        for limit in (200, 500, 1500, DEFAULT_MAX_CHARACTERS):
            capsule = build_capsule(speaker(), partner_character_id="ppr-b", max_characters=limit)
            self.assertLessEqual(capsule.characters, max(limit, len(capsule.text)))

    def test_always_fragments_survive_a_tiny_budget(self):
        capsule = build_capsule(speaker(), partner_character_id="ppr-b", max_characters=50)
        headings = {f.heading for f in capsule.included}
        self.assertIn("固定プロフィール", headings)
        self.assertIn("人物ごとの感情・関係性", headings)

    def test_omitted_fragments_are_reported_not_discarded_silently(self):
        capsule = build_capsule(speaker(), partner_character_id="ppr-b", max_characters=300)
        self.assertTrue(capsule.truncated)
        self.assertGreater(capsule.summary()["omitted_fragments"], 0)
        self.assertTrue(capsule.summary()["omitted_headings"])

    def test_a_short_persona_is_not_truncated(self):
        small = speaker(basic_settings="【核】\n短い。\n", speaking_style="", dialogue_samples="")
        self.assertFalse(build_capsule(small, partner_character_id="ppr-b").truncated)

    def test_capsule_is_deterministic(self):
        first = build_capsule(speaker(), partner_character_id="ppr-b")
        second = build_capsule(speaker(), partner_character_id="ppr-b")
        self.assertEqual(first.text, second.text)

    def test_the_partner_relation_is_the_one_for_this_partner_only(self):
        capsule = build_capsule(speaker(), partner_character_id="zzz")
        self.assertNotIn("相手への関係の本文", capsule.text)

    def test_no_llm_summary_marker_appears(self):
        capsule = build_capsule(speaker(), partner_character_id="ppr-b")
        self.assertIn("完全な行または文だけを選んだ", capsule.text)


class CategorySelection(unittest.TestCase):
    def test_candidates_are_small_categories_only(self):
        candidates = topic_category_candidates(speaker())
        self.assertEqual(
            list(candidates), ["人類/人間の本性", "社会/都市", "技術/交通", "言語/沈黙"]
        )
        self.assertNotIn("人類", candidates)
        self.assertNotIn(VALUE_BOUNDARY_CATEGORY, candidates)

    def test_blank_stances_are_not_offered(self):
        self.assertNotIn("芸術/音楽", topic_category_candidates(speaker()))

    def test_unknown_names_are_reported_not_guessed(self):
        resolved, unknown = resolve_selected_categories(speaker(), ["人類/人間の本性", "存在しない"])
        self.assertEqual([r.category for r in resolved], ["人類/人間の本性"])
        self.assertEqual(unknown, ("存在しない",))

    def test_near_miss_names_are_not_matched(self):
        _, unknown = resolve_selected_categories(speaker(), ["人間の本性"])
        self.assertEqual(unknown, ("人間の本性",))

    def test_duplicates_are_collapsed(self):
        resolved, _ = resolve_selected_categories(speaker(), ["社会/都市", "社会/都市"])
        self.assertEqual(len(resolved), 1)

    def test_selection_is_capped(self):
        names = ["人類/人間の本性", "社会/都市", "技術/交通", "言語/沈黙"]
        resolved, _ = resolve_selected_categories(speaker(), names, maximum=2)
        self.assertEqual(len(resolved), 2)
        self.assertEqual(MAX_SELECTED_CATEGORIES, 4)


class ContextAssembly(unittest.TestCase):
    def _context(self, **kw):
        return build_persona_context(speaker(), partner(), **kw)

    def test_is_deterministic(self):
        names = ("社会/都市",)
        self.assertEqual(
            self._context(selected_category_names=names).text,
            self._context(selected_category_names=names).text,
        )

    def test_value_boundary_is_passed_verbatim(self):
        self.assertIn(BOUNDARY, self._context().text)

    def test_applied_modes_are_stated_and_others_excluded(self):
        text = self._context().text
        self.assertIn("議論と意見交換 に関する指示だけです", text)
        self.assertIn("解説・相談 に関する指示は適用しません", text)

    def test_boundary_invariants_survive_mode_selection(self):
        text = self._context().text
        self.assertIn("保護対象、忌避価値、反射的反論、同意境界は、モードに関わらず維持", text)

    def test_role_cannot_override_the_personas_judgement(self):
        self.assertIn("人格の価値判断を上書きしません", self._context().text)

    def test_selected_categories_are_included_verbatim(self):
        context = self._context(selected_category_names=("社会/都市",))
        self.assertIn("- 社会/都市: 都市についての本文", context.text)
        self.assertEqual([t.category for t in context.selected_categories], ["社会/都市"])

    def test_unselected_categories_are_absent(self):
        context = self._context(selected_category_names=("社会/都市",))
        self.assertNotIn("交通についての本文", context.text)

    def test_unknown_names_are_recorded_on_the_context(self):
        context = self._context(selected_category_names=("社会/都市", "存在しない"))
        self.assertEqual(context.unknown_category_names, ("存在しない",))
        self.assertIn("存在しない", context.summary()["unknown_category_names"])

    def test_addressing_contract_is_embedded(self):
        self.assertIn("相手さん", self._context().text)

    def test_the_partners_persona_is_not_leaked(self):
        text = self._context().text
        self.assertNotIn("相手の秘密の来歴", text)
        self.assertNotIn("相手の秘密の価値観", text)

    def test_application_boundary_is_present(self):
        text = self._context().text
        self.assertIn("共有した過去や関係性を新しく作らないでください", text)

    def test_revision_identifies_the_persona_version(self):
        from ppr.revisions import revision_id_for

        self.assertEqual(self._context().revision_id, revision_id_for(speaker()))

    def test_summary_records_what_was_actually_sent(self):
        summary = self._context(selected_category_names=("社会/都市",)).summary()
        self.assertEqual(summary["selected_categories"], ["社会/都市"])
        self.assertEqual(summary["applied_modes"], ["議論", "意見交換"])
        self.assertEqual(summary["available_category_count"], 4)
        self.assertIn("capsule", summary)
        self.assertIn("addressing", summary)

    def test_a_persona_without_a_value_boundary_still_builds(self):
        plain = speaker(topic_stances=(TopicStance("t1", "社会/都市", "本文"),))
        context = build_persona_context(plain, partner())
        self.assertEqual(context.value_boundary_text, "")
        self.assertIn("あなたが演じる人物", context.text)


class RealDataContext(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import tempfile
        from pathlib import Path

        from tests.test_real_jts_data import JTS_ROOT

        if JTS_ROOT is None:
            raise unittest.SkipTest("JTSが参照できないためスキップ")
        from ppr.adapters.jts.importer import import_locale
        from ppr.assets import ASSET_PERSONA_IDS

        cls.tmp = tempfile.TemporaryDirectory()
        result = import_locale(
            JTS_ROOT, "ja-JP", persona_ids=ASSET_PERSONA_IDS, imports_dir=Path(cls.tmp.name)
        )
        cls.docs = {r.persona_id: r.variants["ja-JP"] for r in result.records}

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, "tmp"):
            cls.tmp.cleanup()

    def test_every_persona_offers_172_candidates(self):
        for persona_id, document in self.docs.items():
            self.assertEqual(len(topic_category_candidates(document)), 172, persona_id)

    def test_compact_capsule_fits_and_keeps_the_core(self):
        """32k向けに削るときでも、固定・概要・核・口調のどれも欠けない。"""
        ids = list(self.docs)
        for persona_id, document in self.docs.items():
            other = next(p for p in ids if p != persona_id)
            capsule = build_capsule(
                document, partner_character_id=other, max_characters=COMPACT_MAX_CHARACTERS
            )
            self.assertLessEqual(capsule.characters, COMPACT_MAX_CHARACTERS, persona_id)
            priorities = {f.priority for f in capsule.included}
            self.assertTrue({0, 1, 2, 3}.issubset(priorities), f"{persona_id}: {priorities}")

    def test_default_budget_passes_every_persona_in_full(self):
        """既定の予算では、8人とも原典を削らずに渡せる。"""
        ids = list(self.docs)
        for persona_id, document in self.docs.items():
            other = next(p for p in ids if p != persona_id)
            rendering = build_persona_rendering(document, partner_character_id=other)
            self.assertEqual(rendering.mode, MODE_FULL, persona_id)
            self.assertFalse(rendering.truncated, persona_id)
            for field in ("overview", "basic_settings", "speaking_style", "dialogue_samples"):
                body = getattr(document, field)
                if body.strip():
                    self.assertIn(body, rendering.text, f"{persona_id}.{field}")

    def test_every_context_carries_the_value_boundary_verbatim(self):
        ids = list(self.docs)
        for persona_id, document in self.docs.items():
            other = self.docs[next(p for p in ids if p != persona_id)]
            context = build_persona_context(document, other)
            boundary = document.value_boundary
            self.assertIsNotNone(boundary)
            self.assertIn(boundary.stance, context.text, persona_id)

    def test_two_speakers_fit_a_wide_context(self):
        """既定の全文モードでも、2人分が広い文脈に収まる。"""
        from ppr.rehearsal.capsule import TOKENS_PER_CHARACTER

        sizes = []
        ids = list(self.docs)
        for persona_id, document in self.docs.items():
            other = self.docs[next(p for p in ids if p != persona_id)]
            context = build_persona_context(
                document, other, selected_category_names=("人類/人間の本性", "社会/都市")
            )
            sizes.append(context.characters)
        worst = sum(sorted(sizes)[-2:])
        self.assertLess(worst * TOKENS_PER_CHARACTER, 131072 * 0.7)

    def test_compact_budget_switches_every_persona_to_a_capsule(self):
        ids = list(self.docs)
        for persona_id, document in self.docs.items():
            other = self.docs[next(p for p in ids if p != persona_id)]
            context = build_persona_context(
                document, other, max_persona_characters=COMPACT_MAX_CHARACTERS
            )
            self.assertEqual(context.capsule.mode, MODE_CAPSULE, persona_id)
            self.assertIn(document.value_boundary.stance, context.text, persona_id)


class RenderingMode(unittest.TestCase):
    def test_full_when_the_budget_is_enough(self):
        rendering = build_persona_rendering(speaker(), partner_character_id="ppr-b")
        self.assertEqual(rendering.mode, MODE_FULL)
        self.assertFalse(rendering.truncated)

    def test_capsule_when_the_budget_is_not_enough(self):
        rendering = build_persona_rendering(
            speaker(), partner_character_id="ppr-b", max_characters=80
        )
        self.assertEqual(rendering.mode, MODE_CAPSULE)
        self.assertTrue(rendering.truncated)

    def test_full_rendering_is_lossless(self):
        document = speaker()
        text = render_full_persona(document, partner_character_id="ppr-b")
        for field in ("overview", "basic_settings", "speaking_style", "dialogue_samples"):
            self.assertIn(getattr(document, field), text)
        self.assertIn("相手への関係の本文", text)

    def test_full_rendering_says_nothing_was_omitted(self):
        text = render_full_persona(speaker(), partner_character_id="ppr-b")
        self.assertIn("省略も要約もしていない", text)

    def test_full_rendering_excludes_other_partners_relations(self):
        document = speaker(person_stances=(
            PersonStance("p1", "別人", "別人への関係", "zzz", "", ""),
        ))
        self.assertNotIn("別人への関係", render_full_persona(document, partner_character_id="ppr-b"))

    def test_mode_is_recorded_in_the_summary(self):
        context = build_persona_context(speaker(), partner())
        self.assertEqual(context.summary()["capsule"]["mode"], MODE_FULL)

    def test_budget_scales_with_the_context_limit(self):
        # 32kでは実質コンパクト相当まで落ちる
        self.assertLess(persona_budget_for(32768), COMPACT_MAX_CHARACTERS * 1.5)
        self.assertGreater(persona_budget_for(200000), persona_budget_for(131072))
        self.assertGreater(persona_budget_for(131072), COMPACT_MAX_CHARACTERS)

    def test_budget_never_falls_below_the_compact_floor(self):
        for limit in (0, -1, 1000, 8192):
            self.assertGreaterEqual(persona_budget_for(limit), COMPACT_MAX_CHARACTERS)

    def test_more_speakers_get_a_smaller_share_each(self):
        self.assertLess(persona_budget_for(400000, speakers=4), persona_budget_for(400000, speakers=2))
