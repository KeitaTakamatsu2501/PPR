import tempfile
import unittest
from pathlib import Path

from ppr.domain import VALUE_BOUNDARY_CATEGORY, Library, PersonaDocument, PersonaRecord, PersonStance, TopicStance
from ppr.llm.fake import CancellingClient, FakeLanguageModelClient
from ppr.rehearsal.capsule import COMPACT_MAX_CHARACTERS
from ppr.rehearsal.contracts import (
    SELECTION_MANUAL,
    SELECTION_MODEL,
    SELECTION_NONE,
    STATUS_CANCELLED,
    STATUS_FAILED,
    STATUS_SUCCESS,
    CastMember,
    ContextOptions,
    ModelSettings,
    RehearsalRequest,
    Scenario,
    SchemeOutcome,
    Utterance,
)
from ppr.rehearsal.runner import CATEGORY_SELECTION_SCHEMA_NAME, RehearsalRunner
from ppr.revisions import RevisionStore, revision_id_for

SELECTION = {"categories": ["社会/都市", "技術/交通"]}


def document(pid: str, name: str, **kw) -> PersonaDocument:
    base = dict(
        persona_id=pid, locale="ja-JP", name=name, first_person="私", second_person="あなた",
        humanity_membership="included", overview=f"{name}の概要",
        basic_settings=f"【人格の核】\n{name}の核。\n",
        speaking_style="【声】\n落ち着いている。\n",
        dialogue_samples="【例】\n「こんにちは。」\n",
        topic_stances=(
            TopicStance("t1", "社会/都市", f"{name}の都市観"),
            TopicStance("t2", "技術/交通", f"{name}の交通観"),
            TopicStance("t3", "芸術/音楽", f"{name}の音楽観"),
            TopicStance("t9", VALUE_BOUNDARY_CATEGORY, "【恒常的な忌避価値】道具扱いを嫌う。"),
        ),
        person_stances=(PersonStance("p1", "相手", "関係の本文", "ppr-b", "相手さん", ""),),
    )
    base.update(kw)
    return PersonaDocument(**base)


def library() -> Library:
    return (
        Library()
        .with_persona(PersonaRecord("ppr-a", {"ja-JP": document("ppr-a", "話者A")}))
        .with_persona(PersonaRecord("ppr-b", {"ja-JP": document("ppr-b", "話者B")}))
    )


def request(**kw) -> RehearsalRequest:
    base = dict(
        scheme_id="test-scheme", scheme_version=1,
        cast=(
            CastMember("a", "ppr-a", "ja-JP"),
            CastMember("b", "ppr-b", "ja-JP"),
        ),
        scenario=Scenario(topic="湿地を埋めて道路を延ばす案", material="反対する人は成長の邪魔だ"),
        role_text="短い会話を作ってください。",
        model_settings=ModelSettings(),
    )
    base.update(kw)
    return RehearsalRequest(**base)


class RecordingAdapter:
    scheme_id = "test-scheme"
    scheme_version = 1
    default_role_text = ""

    def __init__(self, *, fail: Exception | None = None, issues: tuple[str, ...] = ()):
        self.fail = fail
        self.issues = issues
        self.seen_contexts = ()
        self.stage_calls = 0

    def validate(self, request):
        return self.issues

    def run(self, request, contexts, call):
        self.seen_contexts = contexts
        if self.fail is not None:
            raise self.fail
        value = call(
            "draft", [{"role": "user", "content": "生成してください"}],
            schema_name="test_draft", schema={"type": "object"},
        )
        self.stage_calls += 1
        return SchemeOutcome(
            utterances=(Utterance("u1", "a", value.get("text", "")),),
            scheme_output={"echo": value},
        )


def client(**extra) -> FakeLanguageModelClient:
    responses = {
        CATEGORY_SELECTION_SCHEMA_NAME: SELECTION,
        "test_draft": {"text": "生成された発話"},
    }
    responses.update(extra)
    return FakeLanguageModelClient(responses)


class Success(unittest.TestCase):
    def test_run_succeeds_and_records_stages(self):
        adapter = RecordingAdapter()
        result = RehearsalRunner(library(), client()).run(request(), adapter)
        self.assertEqual(result.status, STATUS_SUCCESS, result.errors)
        self.assertEqual([u.text for u in result.utterances], ["生成された発話"])
        stage_ids = [s.stage_id for s in result.stages]
        self.assertEqual(stage_ids, ["category_selection:a", "category_selection:b", "draft"])

    def test_every_stage_records_what_was_sent_and_returned(self):
        result = RehearsalRunner(library(), client()).run(request(), RecordingAdapter())
        for stage in result.stages:
            self.assertGreater(stage.request_characters, 0)
            self.assertIsNotNone(stage.response)
            self.assertTrue(stage.started_at and stage.finished_at)
            self.assertIsNone(stage.error)

    def test_contexts_are_built_for_every_cast_member(self):
        adapter = RecordingAdapter()
        RehearsalRunner(library(), client()).run(request(), adapter)
        self.assertEqual([c.actor_ref for c in adapter.seen_contexts], ["a", "b"])
        self.assertEqual([c.persona_id for c in adapter.seen_contexts], ["ppr-a", "ppr-b"])

    def test_the_result_is_serialisable(self):
        result = RehearsalRunner(library(), client()).run(request(), RecordingAdapter())
        payload = result.to_json()
        self.assertEqual(payload["status"], STATUS_SUCCESS)
        self.assertIn("persona_contexts", payload)
        self.assertEqual(len(payload["stages"]), 3)


class CategorySelection(unittest.TestCase):
    def test_model_selection_is_applied_to_the_context(self):
        adapter = RecordingAdapter()
        RehearsalRunner(library(), client()).run(request(), adapter)
        for context in adapter.seen_contexts:
            self.assertEqual(
                [t.category for t in context.selected_categories], ["社会/都市", "技術/交通"]
            )

    def test_only_category_names_are_sent_never_the_bodies(self):
        fake = client()
        RehearsalRunner(library(), fake).run(request(), RecordingAdapter())
        selection_calls = [
            c for c in fake.calls if c["schema_name"] == CATEGORY_SELECTION_SCHEMA_NAME
        ]
        self.assertEqual(len(selection_calls), 2)
        sent = "\n".join(m["content"] for m in selection_calls[0]["messages"])
        self.assertIn("社会/都市", sent)
        self.assertNotIn("の都市観", sent)

    def test_unknown_names_are_warned_and_ignored(self):
        fake = client(**{CATEGORY_SELECTION_SCHEMA_NAME: {"categories": ["存在しない分類"]}})
        adapter = RecordingAdapter()
        result = RehearsalRunner(library(), fake).run(request(), adapter)
        self.assertEqual(result.status, STATUS_SUCCESS)
        self.assertTrue(any("一覧にないカテゴリ名" in w for w in result.warnings))
        self.assertEqual(adapter.seen_contexts[0].selected_categories, ())

    def test_selection_failure_does_not_stop_the_run(self):
        fake = client(**{CATEGORY_SELECTION_SCHEMA_NAME: RuntimeError("provider error")})
        result = RehearsalRunner(library(), fake).run(request(), RecordingAdapter())
        self.assertEqual(result.status, STATUS_SUCCESS)
        self.assertTrue(any("価値観の選択に失敗" in w for w in result.warnings))

    def test_manual_selection_skips_the_model(self):
        fake = client()
        options = ContextOptions(
            category_selection=SELECTION_MANUAL,
            manual_categories={"a": ("芸術/音楽",), "b": ()},
        )
        adapter = RecordingAdapter()
        RehearsalRunner(library(), fake).run(request(context_options=options), adapter)
        self.assertEqual(
            [c["schema_name"] for c in fake.calls], ["test_draft"]
        )
        self.assertEqual(
            [t.category for t in adapter.seen_contexts[0].selected_categories], ["芸術/音楽"]
        )

    def test_selection_none_sends_no_categories(self):
        adapter = RecordingAdapter()
        RehearsalRunner(library(), client()).run(
            request(context_options=ContextOptions(category_selection=SELECTION_NONE)), adapter
        )
        self.assertEqual(adapter.seen_contexts[0].selected_categories, ())


class PersonaResolution(unittest.TestCase):
    def test_a_saved_revision_is_used_when_given(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = RevisionStore(Path(tmp))
            saved = store.create(document("ppr-a", "話者A", overview="保存された版の概要"))
            cast = (
                CastMember("a", "ppr-a", "ja-JP", saved.revision_id),
                CastMember("b", "ppr-b", "ja-JP"),
            )
            adapter = RecordingAdapter()
            RehearsalRunner(library(), client(), revisions=store).run(
                request(cast=cast), adapter
            )
            self.assertEqual(adapter.seen_contexts[0].revision_id, saved.revision_id)

    def test_an_unknown_revision_is_refused(self):
        cast = (
            CastMember("a", "ppr-a", "ja-JP", "0" * 64),
            CastMember("b", "ppr-b", "ja-JP"),
        )
        result = RehearsalRunner(library(), client()).run(request(cast=cast), RecordingAdapter())
        self.assertEqual(result.status, STATUS_FAILED)
        self.assertTrue(any("版が保存されていません" in e for e in result.errors))

    def test_a_matching_revision_without_a_store_is_accepted(self):
        expected = revision_id_for(library().persona("ppr-a").variants["ja-JP"])
        cast = (
            CastMember("a", "ppr-a", "ja-JP", expected),
            CastMember("b", "ppr-b", "ja-JP"),
        )
        result = RehearsalRunner(library(), client()).run(request(cast=cast), RecordingAdapter())
        self.assertEqual(result.status, STATUS_SUCCESS, result.errors)

    def test_a_missing_persona_fails_cleanly(self):
        cast = (CastMember("a", "missing", "ja-JP"), CastMember("b", "ppr-b", "ja-JP"))
        result = RehearsalRunner(library(), client()).run(request(cast=cast), RecordingAdapter())
        self.assertEqual(result.status, STATUS_FAILED)
        self.assertTrue(any("人格が見つかりません" in e for e in result.errors))

    def test_the_library_is_not_modified_by_a_run(self):
        lib = library()
        before = lib.to_json()
        RehearsalRunner(lib, client()).run(request(), RecordingAdapter())
        self.assertEqual(lib.to_json(), before)


class Budget(unittest.TestCase):
    def test_explicit_budget_is_honoured(self):
        options = ContextOptions(max_persona_characters=COMPACT_MAX_CHARACTERS)
        adapter = RecordingAdapter()
        RehearsalRunner(library(), client()).run(request(context_options=options), adapter)
        self.assertLessEqual(
            adapter.seen_contexts[0].capsule.characters, COMPACT_MAX_CHARACTERS
        )

    def test_budget_is_derived_from_the_context_limit_when_unset(self):
        options = ContextOptions(max_persona_characters=None)
        settings = ModelSettings(context_limit=200000)
        adapter = RecordingAdapter()
        RehearsalRunner(library(), client()).run(
            request(context_options=options, model_settings=settings), adapter
        )
        self.assertEqual(adapter.seen_contexts[0].capsule.mode, "full")

    def test_truncation_is_warned(self):
        options = ContextOptions(max_persona_characters=120)
        result = RehearsalRunner(library(), client()).run(
            request(context_options=options), RecordingAdapter()
        )
        self.assertTrue(any("渡していません" in w for w in result.warnings))


class Failures(unittest.TestCase):
    def test_cancel_stops_the_run(self):
        result = RehearsalRunner(
            library(), client(), should_cancel=lambda: True
        ).run(request(), RecordingAdapter())
        self.assertEqual(result.status, STATUS_CANCELLED)
        self.assertEqual(result.stages, [])

    def test_cancel_midway_is_recorded_as_cancelled(self):
        fake = CancellingClient({CATEGORY_SELECTION_SCHEMA_NAME: SELECTION}, cancel_at=2)
        result = RehearsalRunner(library(), fake).run(request(), RecordingAdapter())
        self.assertEqual(result.status, STATUS_CANCELLED)

    def test_scheme_validation_issues_stop_the_run(self):
        adapter = RecordingAdapter(issues=("作品固有の入力が不正です。",))
        result = RehearsalRunner(library(), client()).run(request(), adapter)
        self.assertEqual(result.status, STATUS_FAILED)
        self.assertTrue(any("作品固有" in e for e in result.errors))

    def test_request_validation_catches_a_duplicate_actor(self):
        cast = (CastMember("a", "ppr-a", "ja-JP"), CastMember("a", "ppr-b", "ja-JP"))
        result = RehearsalRunner(library(), client()).run(request(cast=cast), RecordingAdapter())
        self.assertEqual(result.status, STATUS_FAILED)
        self.assertTrue(any("配役が重複" in e for e in result.errors))

    def test_an_adapter_failure_is_recorded_not_swallowed(self):
        adapter = RecordingAdapter(fail=ValueError("構成作家が失敗しました"))
        result = RehearsalRunner(library(), client()).run(request(), adapter)
        self.assertEqual(result.status, STATUS_FAILED)
        self.assertTrue(any("構成作家が失敗" in e for e in result.errors))
        self.assertEqual(len(result.stages), 2)  # 選択2件までは記録に残る

    def test_a_provider_error_is_recorded_on_the_stage(self):
        fake = client(test_draft=RuntimeError("provider down"))
        result = RehearsalRunner(library(), fake).run(request(), RecordingAdapter())
        self.assertEqual(result.status, STATUS_FAILED)
        self.assertEqual(result.stages[-1].stage_id, "draft")
        self.assertIn("provider down", result.stages[-1].error)
