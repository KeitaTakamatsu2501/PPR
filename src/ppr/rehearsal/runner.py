"""試演の実行。

runner が担当するのは次だけ。

- 配役から保存済みの人格を読む（編集中のGUI stateは読まない）
- 小カテゴリの選択をモデルへ問い合わせる
- 人格contextを組み立てる
- provider を呼び、送ったものと返ったものを記録する
- キャンセルを扱う

作品固有の判断は一切持たない。台詞の形式、返信の構造、件数の妥当性は adapter の責務。
"""

from __future__ import annotations

import datetime as _dt
import json
from dataclasses import dataclass
from typing import Any, Callable

from ..domain import Library, PersonaDocument
from ..revisions import RevisionStore, revision_id_for
from .capsule import persona_budget_for
from .context import PersonaContext, build_persona_context, topic_category_candidates
from .contracts import (
    SELECTION_MANUAL,
    SELECTION_MODEL,
    STATUS_CANCELLED,
    STATUS_FAILED,
    STATUS_SUCCESS,
    CastMember,
    LanguageModelClient,
    RehearsalCancelled,
    RehearsalRequest,
    RehearsalResult,
    SchemeAdapter,
    SchemeInputError,
    StageRecord,
    new_run_id,
)

CATEGORY_SELECTION_STAGE = "category_selection"
CATEGORY_SELECTION_SCHEMA_NAME = "ppr_topic_category_selection"


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat()


def category_selection_schema(maximum: int) -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["categories"],
        "properties": {
            "categories": {
                "type": "array",
                "minItems": 0,
                "maxItems": maximum,
                "items": {"type": "string"},
            }
        },
    }


def category_selection_messages(
    *, name: str, topic: str, material: str, candidates: tuple[str, ...], maximum: int
) -> list[dict[str, str]]:
    """候補はカテゴリ名だけを渡す。本文は渡さない。"""
    listing = json.dumps(list(candidates), ensure_ascii=False, indent=1)
    material_block = f"\n【素材】\n{material}\n" if material.strip() else ""
    return [
        {
            "role": "system",
            "content": (
                "あなたは会話の題材と、ある人物の価値観カテゴリの一覧を見比べて、"
                "その題材に関係するカテゴリだけを選ぶ担当です。"
                "指定されたJSONスキーマだけを返してください。"
            ),
        },
        {
            "role": "user",
            "content": (
                f"【人物】{name}\n"
                f"【話題】{topic}\n"
                f"{material_block}"
                f"\n【選べるカテゴリ】一覧にある文字列をそのまま使ってください。"
                f"一覧にない名前を作らないでください。\n{listing}\n\n"
                f"この話題を語るうえで、この人物の立場が実際に効いてくるカテゴリを"
                f"最大{maximum}件選んでください。関係が薄いなら少なくて構いません。"
                f"該当が無ければ空の配列を返してください。"
            ),
        },
    ]


@dataclass
class _RunState:
    result: RehearsalResult
    cancelled: bool = False


class RehearsalRunner:
    def __init__(
        self,
        library: Library,
        client: LanguageModelClient,
        *,
        revisions: RevisionStore | None = None,
        on_status: Callable[[str], None] | None = None,
        should_cancel: Callable[[], bool] | None = None,
    ) -> None:
        self.library = library
        self.client = client
        self.revisions = revisions
        self.on_status = on_status or (lambda _m: None)
        self.should_cancel = should_cancel or (lambda: False)

    # ---- 人格の読み出し -----------------------------------------------

    def resolve_document(self, member: CastMember) -> PersonaDocument:
        """保存済みの版を読む。編集中の内容は読まない。"""
        if member.revision_id and self.revisions is not None:
            if self.revisions.exists(member.revision_id):
                return self.revisions.load(member.revision_id).document
        record = self.library.persona(member.persona_id)
        if record is None:
            raise SchemeInputError(f"人格が見つかりません: {member.persona_id}")
        document = record.document(member.locale)
        if document is None:
            raise SchemeInputError(
                f"localeが見つかりません: {member.persona_id} / {member.locale}"
            )
        if member.revision_id and revision_id_for(document) != member.revision_id:
            raise SchemeInputError(
                f"要求された版が保存されていません: {member.persona_id} / {member.revision_id[:12]}…"
            )
        return document

    # ---- 実行 ---------------------------------------------------------

    def run(self, request: RehearsalRequest, adapter: SchemeAdapter) -> RehearsalResult:
        result = RehearsalResult(
            run_id=new_run_id(), request=request, status=STATUS_FAILED, started_at=_now()
        )
        state = _RunState(result=result)
        try:
            issues = list(request.validation_issues()) + list(adapter.validate(request))
            if issues:
                raise SchemeInputError("；".join(issues))

            documents = {c.actor_ref: self.resolve_document(c) for c in request.cast}
            contexts = self._build_contexts(request, documents, state)
            result.persona_contexts = contexts

            self.on_status("構成作家へ渡しています")
            outcome = adapter.run(request, contexts, self._make_call(request, state))
            result.utterances = outcome.utterances
            result.scheme_output = dict(outcome.scheme_output)
            result.warnings.extend(outcome.warnings)
            result.status = STATUS_SUCCESS
        except RehearsalCancelled as exc:
            result.status = STATUS_CANCELLED
            result.errors.append(str(exc) or "中止しました。")
        except Exception as exc:  # noqa: BLE001 — 失敗も記録として残す
            result.status = STATUS_FAILED
            result.errors.append(f"{type(exc).__name__}: {exc}")
        finally:
            result.finished_at = _now()
        return result

    # ---- context の生成 -----------------------------------------------

    def _persona_budget(self, request: RehearsalRequest) -> int:
        configured = request.context_options.max_persona_characters
        if configured is not None:
            return configured
        return persona_budget_for(
            request.model_settings.context_limit, speakers=max(1, len(request.cast))
        )

    def _build_contexts(
        self,
        request: RehearsalRequest,
        documents: dict[str, PersonaDocument],
        state: _RunState,
    ) -> tuple[PersonaContext, ...]:
        options = request.context_options
        budget = self._persona_budget(request)
        contexts: list[PersonaContext] = []
        for member in request.cast:
            document = documents[member.actor_ref]
            partner = self._partner_for(member, request, documents)
            names = self._category_names(request, member, document, state)
            context = build_persona_context(
                document,
                partner,
                actor_ref=member.actor_ref,
                selected_category_names=names,
                max_persona_characters=budget,
                applied_modes=options.applied_modes,
                maximum_categories=options.maximum_categories,
            )
            if context.unknown_category_names:
                state.result.warnings.append(
                    f"{document.name}: 一覧にないカテゴリ名が返されました"
                    f"（{', '.join(context.unknown_category_names)}）。無視しました。"
                )
            if context.capsule.truncated:
                state.result.warnings.append(
                    f"{document.name}: 人格が予算に収まらず "
                    f"{len(context.capsule.omitted)} 断片を渡していません。"
                )
            contexts.append(context)
        return tuple(contexts)

    @staticmethod
    def _partner_for(
        member: CastMember, request: RehearsalRequest, documents: dict[str, PersonaDocument]
    ) -> PersonaDocument:
        for other in request.cast:
            if other.actor_ref != member.actor_ref:
                return documents[other.actor_ref]
        return documents[member.actor_ref]

    def _category_names(
        self,
        request: RehearsalRequest,
        member: CastMember,
        document: PersonaDocument,
        state: _RunState,
    ) -> tuple[str, ...]:
        options = request.context_options
        if options.category_selection == SELECTION_MANUAL:
            return tuple(options.manual_categories.get(member.actor_ref, ()))
        if options.category_selection != SELECTION_MODEL:
            return ()

        candidates = topic_category_candidates(document)
        if not candidates:
            return ()
        self.on_status(f"{document.name} に関わる価値観を選んでいます")
        messages = category_selection_messages(
            name=document.name,
            topic=request.scenario.topic,
            material=request.scenario.material,
            candidates=candidates,
            maximum=options.maximum_categories,
        )
        try:
            value = self._call(
                request,
                state,
                f"{CATEGORY_SELECTION_STAGE}:{member.actor_ref}",
                messages,
                schema_name=CATEGORY_SELECTION_SCHEMA_NAME,
                schema=category_selection_schema(options.maximum_categories),
                temperature=0.2,
                max_tokens=512,
            )
        except RehearsalCancelled:
            raise
        except Exception as exc:  # noqa: BLE001
            state.result.warnings.append(
                f"{document.name}: 価値観の選択に失敗しました（{type(exc).__name__}）。"
                "カテゴリなしで続行します。"
            )
            return ()
        if not isinstance(value, dict):
            return ()
        names = value.get("categories")
        if not isinstance(names, list):
            return ()
        return tuple(str(n) for n in names)

    # ---- provider 呼び出し ---------------------------------------------

    def _checkpoint(self) -> None:
        if self.should_cancel():
            raise RehearsalCancelled("作者が中止しました。")

    def _make_call(self, request: RehearsalRequest, state: _RunState):
        def call(
            stage_id: str,
            messages: list[dict[str, str]],
            *,
            schema_name: str = "",
            schema: dict[str, Any] | None = None,
            temperature: float | None = None,
            max_tokens: int | None = None,
        ) -> Any:
            return self._call(
                request, state, stage_id, messages,
                schema_name=schema_name, schema=schema,
                temperature=temperature, max_tokens=max_tokens,
            )

        return call

    def _call(
        self,
        request: RehearsalRequest,
        state: _RunState,
        stage_id: str,
        messages: list[dict[str, str]],
        *,
        schema_name: str,
        schema: dict[str, Any] | None,
        temperature: float | None,
        max_tokens: int | None,
    ) -> Any:
        self._checkpoint()
        settings = request.model_settings
        record = StageRecord(
            stage_id=stage_id,
            model_id=settings.inference_id,
            messages=[dict(m) for m in messages],
            request_characters=sum(len(str(m.get("content", ""))) for m in messages),
            schema_name=schema_name,
            started_at=_now(),
        )
        state.result.stages.append(record)
        try:
            value = self.client.chat_json(
                settings.inference_id,
                messages,
                settings.temperature if temperature is None else temperature,
                settings.max_tokens if max_tokens is None else max_tokens,
                schema_name=schema_name,
                schema=schema or {},
            )
        except Exception as exc:  # noqa: BLE001
            record.error = f"{type(exc).__name__}: {exc}"
            record.finished_at = _now()
            raise
        record.response = value
        record.response_characters = (
            len(value) if isinstance(value, str)
            else len(json.dumps(value, ensure_ascii=False, default=str))
        )
        record.finished_at = _now()
        self._checkpoint()
        return value
