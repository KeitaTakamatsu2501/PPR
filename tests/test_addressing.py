import unittest

from ppr.domain import PersonaDocument, PersonStance
from ppr.rehearsal.addressing import (
    SOURCE_DEFAULT,
    SOURCE_FALLBACK,
    SOURCE_PROFILE,
    SOURCE_RELATION,
    resolve_contract,
    single_line,
)


def persona(pid, name, **kw) -> PersonaDocument:
    base = dict(persona_id=pid, locale="ja-JP", name=name,
                first_person="私", second_person="あなた", humanity_membership="")
    base.update(kw)
    return PersonaDocument(**base)


class DirectAddress(unittest.TestCase):
    def test_relation_override_wins(self):
        speaker = persona("a", "話者", person_stances=(
            PersonStance("e1", "相手", "本文", "b", "相手さん", ""),
        ))
        contract = resolve_contract(speaker, persona("b", "相手"))
        self.assertEqual(contract.direct_address, "相手さん")
        self.assertEqual(contract.direct_address_source, SOURCE_RELATION)

    def test_falls_back_to_the_speakers_second_person(self):
        contract = resolve_contract(persona("a", "話者", second_person="君"), persona("b", "相手"))
        self.assertEqual(contract.direct_address, "君")
        self.assertEqual(contract.direct_address_source, SOURCE_DEFAULT)

    def test_falls_back_to_a_neutral_default_when_nothing_is_set(self):
        contract = resolve_contract(persona("a", "話者", second_person=""), persona("b", "相手"))
        self.assertEqual(contract.direct_address, "あなた")
        self.assertEqual(contract.direct_address_source, SOURCE_FALLBACK)

    def test_override_for_another_partner_is_not_used(self):
        speaker = persona("a", "話者", person_stances=(
            PersonStance("e1", "別人", "本文", "zzz", "別人さん", ""),
        ))
        contract = resolve_contract(speaker, persona("b", "相手"))
        self.assertEqual(contract.direct_address, "あなた")
        self.assertEqual(contract.direct_address_source, SOURCE_DEFAULT)

    def test_multiline_override_is_reduced_to_one_line(self):
        speaker = persona("a", "話者", person_stances=(
            PersonStance("e1", "相手", "", "b", "相手さん\n余計な行", ""),
        ))
        self.assertEqual(resolve_contract(speaker, persona("b", "相手")).direct_address, "相手さん")

    def test_single_line_is_bounded(self):
        self.assertEqual(len(single_line("あ" * 200)), 80)


class PerceivedHumanity(unittest.TestCase):
    def test_relation_override_wins_over_the_profile(self):
        speaker = persona("a", "話者", person_stances=(
            PersonStance("e1", "相手", "", "b", "", "excluded"),
        ))
        contract = resolve_contract(speaker, persona("b", "相手", humanity_membership="conditional"))
        self.assertEqual(contract.perceived_partner_humanity, "excluded")
        self.assertEqual(contract.perceived_source, SOURCE_RELATION)
        self.assertEqual(contract.partner_profile_humanity, "conditional")

    def test_inherit_falls_through_to_the_profile(self):
        speaker = persona("a", "話者", person_stances=(
            PersonStance("e1", "相手", "", "b", "", "inherit"),
        ))
        contract = resolve_contract(speaker, persona("b", "相手", humanity_membership="included"))
        self.assertEqual(contract.perceived_partner_humanity, "included")
        self.assertEqual(contract.perceived_source, SOURCE_PROFILE)

    def test_blank_override_falls_through_to_the_profile(self):
        speaker = persona("a", "話者", person_stances=(
            PersonStance("e1", "相手", "", "b", "", ""),
        ))
        contract = resolve_contract(speaker, persona("b", "相手", humanity_membership="excluded"))
        self.assertEqual(contract.perceived_source, SOURCE_PROFILE)


class RenderedRules(unittest.TestCase):
    def _render(self, speaker_humanity, partner_humanity):
        return resolve_contract(
            persona("a", "話者", humanity_membership=speaker_humanity),
            persona("b", "相手", humanity_membership=partner_humanity),
        ).render()

    def test_excluded_speaker_separates_itself_from_humanity(self):
        self.assertIn("自分を人類から分けた表現", self._render("excluded", "included"))

    def test_included_speaker_may_include_itself(self):
        self.assertIn("自分を人類に含む表現", self._render("included", "excluded"))

    def test_conditional_speaker_must_not_assert(self):
        text = self._render("conditional", "excluded")
        self.assertIn("断定せず", text)
        self.assertNotIn("自分を人類に含む表現", text)

    def test_partner_inside_humanity_is_called_out(self):
        self.assertIn("この相手は人類に含まれます", self._render("excluded", "included"))

    def test_partner_outside_humanity_is_called_out(self):
        self.assertIn("この相手は人類に含まれません", self._render("included", "excluded"))

    def test_undetermined_partner_is_not_decided(self):
        self.assertIn("断定されていません", self._render("included", "conditional"))

    def test_bare_plural_second_person_is_always_forbidden(self):
        for speaker in ("included", "excluded", "conditional"):
            self.assertIn("裸の複数二人称", self._render(speaker, "included"))

    def test_the_contract_does_not_leak_the_partners_persona(self):
        speaker = persona("a", "話者")
        partner = persona("b", "相手", overview="秘密の来歴", basic_settings="秘密の価値観")
        text = resolve_contract(speaker, partner).render()
        self.assertNotIn("秘密の来歴", text)
        self.assertNotIn("秘密の価値観", text)
        self.assertIn("推測しないでください", text)


class RealDataContracts(unittest.TestCase):
    """実データの全ペアで契約が作れること。"""

    @classmethod
    def setUpClass(cls):
        from tests.test_real_jts_data import JTS_ROOT

        if JTS_ROOT is None:
            raise unittest.SkipTest("JTSが参照できないためスキップ")
        from ppr.adapters.jts.importer import import_locale
        from ppr.assets import ASSET_PERSONA_IDS
        import tempfile
        from pathlib import Path

        cls.tmp = tempfile.TemporaryDirectory()
        result = import_locale(
            JTS_ROOT, "ja-JP", persona_ids=ASSET_PERSONA_IDS,
            imports_dir=Path(cls.tmp.name),
        )
        cls.docs = {r.persona_id: r.variants["ja-JP"] for r in result.records}

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, "tmp"):
            cls.tmp.cleanup()

    def test_every_pair_resolves(self):
        pairs = 0
        for speaker in self.docs.values():
            for partner in self.docs.values():
                if speaker.persona_id == partner.persona_id:
                    continue
                contract = resolve_contract(speaker, partner)
                self.assertTrue(contract.direct_address)
                self.assertIn(contract.partner_name, contract.render())
                pairs += 1
        self.assertEqual(pairs, 56)

    def test_address_overrides_are_actually_used(self):
        mira = self.docs["hayamin-2548d9d189"]
        contract = resolve_contract(mira, self.docs["eyja-9ca78ddb88"])
        self.assertEqual(contract.direct_address, "宙星さん")
        self.assertEqual(contract.direct_address_source, SOURCE_RELATION)

    def test_matches_the_contract_observed_in_jts(self):
        """Phase 0 で実際にJTSが生成した契約と主要な値が一致する。"""
        contract = resolve_contract(
            self.docs["h-tsubasa-db3d1d5f64"], self.docs["elmirea-ab93505eb7"]
        )
        self.assertEqual(contract.partner_name, "テルミナス")
        self.assertEqual(contract.partner_species_label, "女神")
        self.assertEqual(contract.direct_address, "テルミナスさん")
        self.assertEqual(contract.first_person, "私")
        self.assertEqual(contract.speaker_humanity, "conditional")
        self.assertEqual(contract.partner_profile_humanity, "excluded")
        self.assertEqual(contract.perceived_partner_humanity, "excluded")
