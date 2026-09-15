import unittest

from ppr.assets import ASSET_PERSONA_IDS, ASSET_PERSONA_NAMES
from ppr.llm.fake import FakeLanguageModelClient
from ppr.rehearsal.contracts import (
    STATUS_FAILED,
    STATUS_SUCCESS,
    CastMember,
    ContextOptions,
    RehearsalRequest,
    Scenario,
)
from ppr.rehearsal.runner import CATEGORY_SELECTION_SCHEMA_NAME, RehearsalRunner
from ppr.rehearsal.schemes import SCHEMES, JtsDialogueScheme, SbtThreadScheme, create_scheme

from .test_rehearsal_runner import library, request

LINES = {
    "lines": [
        {"speaker": "a", "text": "最初の発言。", "emotion": "normal"},
        {"speaker": "b", "text": "それへの返答。", "emotion": "serious"},
        {"speaker": "a", "text": "さらに続ける。", "emotion": "normal"},
        {"speaker": "b", "text": "受け止める。", "emotion": "sad"},
        {"speaker": "a", "text": "論点を進める。", "emotion": "angry"},
        {"speaker": "b", "text": "静かに締める。", "emotion": "normal"},
    ]
}


def jts_client(lines=None) -> FakeLanguageModelClient:
    return FakeLanguageModelClient({
        CATEGORY_SELECTION_SCHEMA_NAME: {"categories": ["社会/都市"]},
        "jts_dialogue_lines": lines if lines is not None else LINES,
    })


def sbt_client(text: str = "これは投稿の本文です。" * 8) -> FakeLanguageModelClient:
    return FakeLanguageModelClient(
        {CATEGORY_SELECTION_SCHEMA_NAME: {"categories": ["社会/都市"]}},
        default={"text": text},
    )


def jts_request(**scheme_input) -> RehearsalRequest:
    return request(scheme_id="jts-dialogue", scheme_version=1, scheme_input=scheme_input)


def sbt_request(**scheme_input) -> RehearsalRequest:
    return request(scheme_id="sbt-thread", scheme_version=1, scheme_input=scheme_input)


class JtsDialogue(unittest.TestCase):
    def test_one_batch_call_produces_the_whole_dialogue(self):
        fake = jts_client()
        result = RehearsalRunner(library(), fake).run(jts_request(), JtsDialogueScheme())
        self.assertEqual(result.status, STATUS_SUCCESS, result.errors)
        self.assertEqual(len(result.utterances), 6)
        dialogue_calls = [c for c in fake.calls if c["schema_name"] == "jts_dialogue_lines"]
        self.assertEqual(len(dialogue_calls), 1)

    def test_speakers_alternate(self):
        result = RehearsalRunner(library(), jts_client()).run(jts_request(), JtsDialogueScheme())
        self.assertEqual([u.actor_ref for u in result.utterances], list("ababab"))

    def test_emotions_are_kept_in_scheme_output_not_in_the_persona(self):
        result = RehearsalRunner(library(), jts_client()).run(jts_request(), JtsDialogueScheme())
        emotions = [line["emotion"] for line in result.scheme_output["lines"]]
        self.assertEqual(emotions, ["normal", "serious", "normal", "sad", "angry", "normal"])
        for context in result.persona_contexts:
            self.assertNotIn("emotion", context.text)

    def test_wrong_count_is_refused(self):
        broken = {"lines": LINES["lines"][:5]}
        result = RehearsalRunner(library(), jts_client(broken)).run(
            jts_request(), JtsDialogueScheme()
        )
        self.assertEqual(result.status, STATUS_FAILED)
        self.assertTrue(any("発言数が一致しません" in e for e in result.errors))

    def test_non_alternating_speaker_is_refused(self):
        broken = {"lines": [dict(line) for line in LINES["lines"]]}
        broken["lines"][1]["speaker"] = "a"
        result = RehearsalRunner(library(), jts_client(broken)).run(
            jts_request(), JtsDialogueScheme()
        )
        self.assertTrue(any("交互になっていません" in e for e in result.errors))

    def test_unknown_emotion_is_refused(self):
        broken = {"lines": [dict(line) for line in LINES["lines"]]}
        broken["lines"][0]["emotion"] = "excited"
        result = RehearsalRunner(library(), jts_client(broken)).run(
            jts_request(), JtsDialogueScheme()
        )
        self.assertTrue(any("感情が許可されていません" in e for e in result.errors))

    def test_empty_text_is_refused(self):
        broken = {"lines": [dict(line) for line in LINES["lines"]]}
        broken["lines"][2]["text"] = "   "
        result = RehearsalRunner(library(), jts_client(broken)).run(
            jts_request(), JtsDialogueScheme()
        )
        self.assertTrue(any("本文が空です" in e for e in result.errors))

    def test_out_of_range_utterance_count_is_refused_before_calling(self):
        fake = jts_client()
        result = RehearsalRunner(library(), fake).run(
            jts_request(utterances=99), JtsDialogueScheme()
        )
        self.assertEqual(result.status, STATUS_FAILED)
        self.assertEqual(fake.calls, [])

    def test_it_does_not_claim_to_reproduce_jts(self):
        result = RehearsalRunner(library(), jts_client()).run(jts_request(), JtsDialogueScheme())
        self.assertIn("再現したものではありません", result.scheme_output["note"])


class SbtThread(unittest.TestCase):
    def test_posts_are_generated_one_at_a_time(self):
        fake = sbt_client()
        result = RehearsalRunner(library(), fake).run(sbt_request(), SbtThreadScheme())
        self.assertEqual(result.status, STATUS_SUCCESS, result.errors)
        stage_ids = [s.stage_id for s in result.stages if not s.stage_id.startswith("category")]
        self.assertEqual(stage_ids, ["root", "reply_01", "reply_02", "reply_03", "reply_04", "reply_05"])

    def test_each_post_gets_its_own_system_prompt(self):
        fake = sbt_client()
        RehearsalRunner(library(), fake).run(sbt_request(), SbtThreadScheme())
        posts = [c for c in fake.calls if c["schema_name"].startswith("sbt_")]
        systems = [c["messages"][0]["content"] for c in posts]
        self.assertIn("話者A", systems[0])
        self.assertIn("話者B", systems[1])
        self.assertNotEqual(systems[0], systems[1])

    def test_replies_target_the_previous_post(self):
        result = RehearsalRunner(library(), sbt_client()).run(sbt_request(), SbtThreadScheme())
        posts = result.scheme_output["posts"]
        self.assertIsNone(posts[0]["reply_to_id"])
        for index in range(1, len(posts)):
            self.assertEqual(posts[index]["reply_to_id"], posts[index - 1]["post_id"])

    def test_post_ids_are_assigned_by_ppr(self):
        result = RehearsalRunner(library(), sbt_client()).run(sbt_request(), SbtThreadScheme())
        ids = [p["post_id"] for p in result.scheme_output["posts"]]
        self.assertEqual(ids, ["p01", "p02", "p03", "p04", "p05", "p06"])

    def test_authors_alternate_starting_with_the_other_speaker(self):
        result = RehearsalRunner(library(), sbt_client()).run(sbt_request(), SbtThreadScheme())
        self.assertEqual(
            [p["actor_ref"] for p in result.scheme_output["posts"]], list("ababab")
        )

    def test_total_posts_is_configurable(self):
        result = RehearsalRunner(library(), sbt_client()).run(
            sbt_request(total_posts=4), SbtThreadScheme()
        )
        self.assertEqual(result.scheme_output["total_posts"], 4)

    def test_out_of_range_total_is_refused_before_calling(self):
        fake = sbt_client()
        result = RehearsalRunner(library(), fake).run(
            sbt_request(total_posts=20), SbtThreadScheme()
        )
        self.assertEqual(result.status, STATUS_FAILED)
        self.assertEqual(fake.calls, [])

    def test_length_outside_the_guide_is_a_warning_not_a_failure(self):
        result = RehearsalRunner(library(), sbt_client("短い")).run(
            sbt_request(), SbtThreadScheme()
        )
        self.assertEqual(result.status, STATUS_SUCCESS)
        self.assertTrue(any("目安から外れました" in w for w in result.warnings))

    def test_length_targets_are_configurable(self):
        result = RehearsalRunner(library(), sbt_client("短い")).run(
            sbt_request(root_length=[1, 10], reply_length=[1, 10]), SbtThreadScheme()
        )
        self.assertEqual([w for w in result.warnings if "目安から外れました" in w], [])

    def test_thread_is_visible_to_later_posts(self):
        fake = sbt_client()
        RehearsalRunner(library(), fake).run(sbt_request(), SbtThreadScheme())
        last = [c for c in fake.calls if c["schema_name"] == "sbt_reply_05"][0]
        self.assertIn("これまでのスレッド", last["messages"][1]["content"])
        self.assertIn("[p05]", last["messages"][1]["content"])

    def test_the_final_post_is_asked_to_close(self):
        fake = sbt_client()
        RehearsalRunner(library(), fake).run(sbt_request(), SbtThreadScheme())
        last = [c for c in fake.calls if c["schema_name"] == "sbt_reply_05"][0]
        self.assertIn("応酬の区切り", last["messages"][1]["content"])
        self.assertIn("勝利宣言も不要", last["messages"][1]["content"])


class SharedPersonaAcrossSchemes(unittest.TestCase):
    """同じ人格版が、方式の違う2作品でキャラID固有分岐なしに通ること。"""

    def test_the_same_revision_runs_in_both_schemes(self):
        from ppr.revisions import revision_id_for

        lib = library()
        expected = {
            "a": revision_id_for(lib.persona("ppr-a").variants["ja-JP"]),
            "b": revision_id_for(lib.persona("ppr-b").variants["ja-JP"]),
        }
        cast = (
            CastMember("a", "ppr-a", "ja-JP", expected["a"]),
            CastMember("b", "ppr-b", "ja-JP", expected["b"]),
        )
        options = ContextOptions(category_selection="none")
        jts = RehearsalRunner(lib, jts_client()).run(
            request(scheme_id="jts-dialogue", scheme_version=1, cast=cast,
                    context_options=options), JtsDialogueScheme()
        )
        sbt = RehearsalRunner(lib, sbt_client()).run(
            request(scheme_id="sbt-thread", scheme_version=1, cast=cast,
                    context_options=options), SbtThreadScheme()
        )
        self.assertEqual(jts.status, STATUS_SUCCESS, jts.errors)
        self.assertEqual(sbt.status, STATUS_SUCCESS, sbt.errors)
        for result in (jts, sbt):
            self.assertEqual(
                {c.actor_ref: c.revision_id for c in result.persona_contexts}, expected
            )

    def test_the_persona_context_text_is_identical_in_both_schemes(self):
        options = ContextOptions(category_selection="none")
        lib = library()
        jts = RehearsalRunner(lib, jts_client()).run(
            request(scheme_id="jts-dialogue", scheme_version=1, context_options=options),
            JtsDialogueScheme(),
        )
        sbt = RehearsalRunner(lib, sbt_client()).run(
            request(scheme_id="sbt-thread", scheme_version=1, context_options=options),
            SbtThreadScheme(),
        )
        self.assertEqual(
            [c.text for c in jts.persona_contexts], [c.text for c in sbt.persona_contexts]
        )

    def test_neither_scheme_branches_on_a_character_identity(self):
        """schemeのソースに人格のIDや名前が現れないこと。"""
        import inspect

        from ppr.rehearsal.schemes import jts_dialogue, sbt_thread

        for module in (jts_dialogue, sbt_thread):
            source = inspect.getsource(module)
            for persona_id in ASSET_PERSONA_IDS:
                self.assertNotIn(persona_id, source, module.__name__)
            for name in ASSET_PERSONA_NAMES.values():
                self.assertNotIn(name, source, module.__name__)
            self.assertNotIn("ヒロアキ", source, module.__name__)

    def test_neither_scheme_modifies_the_library(self):
        lib = library()
        before = lib.to_json()
        RehearsalRunner(lib, jts_client()).run(jts_request(), JtsDialogueScheme())
        RehearsalRunner(lib, sbt_client()).run(sbt_request(), SbtThreadScheme())
        self.assertEqual(lib.to_json(), before)


class Registry(unittest.TestCase):
    def test_both_schemes_are_registered(self):
        self.assertEqual(sorted(SCHEMES), ["jts-dialogue", "sbt-thread"])

    def test_create_by_id(self):
        self.assertIsInstance(create_scheme("jts-dialogue"), JtsDialogueScheme)
        self.assertIsInstance(create_scheme("sbt-thread"), SbtThreadScheme)

    def test_unknown_scheme_is_refused(self):
        with self.assertRaises(KeyError):
            create_scheme("does-not-exist")
