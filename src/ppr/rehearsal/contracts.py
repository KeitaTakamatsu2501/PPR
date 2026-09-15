"""試演の共通契約。

作品に依存しない部分だけを置く。返信ID、発話数、感情ラベルのような作品固有の
値はここに持ち込まない。それらは `scheme_input` と `scheme_output` に入る。

runner は revision の読取、人格contextの生成、provider呼出し、キャンセル、
記録の保存だけを担当する。作品固有の判断は SchemeAdapter が持つ。
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass, field
from typing import Any, Protocol

from ..domain import DEFAULT_APPLIED_MODES
from .capsule import DEFAULT_MAX_CHARACTERS
from .context import MAX_SELECTED_CATEGORIES, PersonaContext

STATUS_SUCCESS = "success"
STATUS_FAILED = "failed"
STATUS_CANCELLED = "cancelled"

#: 素材の種別。出演者本人の主張として扱わないために区別する。
MATERIAL_KINDS = ("third_party_claim", "observation", "quoted_criticism")

#: 小カテゴリの選び方。
SELECTION_MODEL = "model"
SELECTION_MANUAL = "manual"
SELECTION_NONE = "none"


def new_request_id() -> str:
    return f"req-{uuid.uuid4().hex[:12]}"


def new_run_id() -> str:
    return f"run-{uuid.uuid4().hex[:12]}"


def digest_of(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class RehearsalCancelled(RuntimeError):
    """作者が中止した。"""


class SchemeInputError(ValueError):
    """作品側の入力が契約を満たしていない。"""


@dataclass(frozen=True)
class CastMember:
    """今回の配役。`actor_ref` は配役、`persona_id` は人物自身。"""

    actor_ref: str
    persona_id: str
    locale: str
    revision_id: str = ""

    def to_json(self) -> dict[str, Any]:
        return {
            "actor_ref": self.actor_ref,
            "persona_id": self.persona_id,
            "locale": self.locale,
            "revision_id": self.revision_id,
        }


@dataclass(frozen=True)
class Scenario:
    """試演の素材。出演者の信条として扱わない。"""

    topic: str
    material: str = ""
    material_kind: str = "third_party_claim"
    material_source: str = "架空の第三者"

    def to_json(self) -> dict[str, Any]:
        return {
            "topic": self.topic,
            "material": self.material,
            "material_kind": self.material_kind,
            "material_source": self.material_source,
        }


@dataclass(frozen=True)
class ModelSettings:
    provider: str = "fake"
    model_key: str = "fake-model"
    inference_id: str = "fake-model"
    temperature: float = 0.7
    max_tokens: int = 4096
    context_limit: int = 131072

    def to_json(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "model_key": self.model_key,
            "inference_id": self.inference_id,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "context_limit": self.context_limit,
        }


@dataclass(frozen=True)
class ContextOptions:
    """人格contextの作り方。作品によらず同じ意味を持つ。"""

    max_persona_characters: int | None = DEFAULT_MAX_CHARACTERS
    applied_modes: tuple[str, ...] = DEFAULT_APPLIED_MODES
    maximum_categories: int = MAX_SELECTED_CATEGORIES
    category_selection: str = SELECTION_MODEL
    manual_categories: dict[str, tuple[str, ...]] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        return {
            "max_persona_characters": self.max_persona_characters,
            "applied_modes": list(self.applied_modes),
            "maximum_categories": self.maximum_categories,
            "category_selection": self.category_selection,
            "manual_categories": {k: list(v) for k, v in self.manual_categories.items()},
        }


@dataclass(frozen=True)
class RehearsalRequest:
    scheme_id: str
    scheme_version: int
    cast: tuple[CastMember, ...]
    scenario: Scenario
    role_text: str = ""
    request_id: str = field(default_factory=new_request_id)
    model_settings: ModelSettings = field(default_factory=ModelSettings)
    context_options: ContextOptions = field(default_factory=ContextOptions)
    scheme_input: dict[str, Any] = field(default_factory=dict)

    @property
    def role_digest(self) -> str:
        return digest_of(self.role_text)

    def actor(self, actor_ref: str) -> CastMember | None:
        return next((c for c in self.cast if c.actor_ref == actor_ref), None)

    def validation_issues(self) -> tuple[str, ...]:
        issues: list[str] = []
        if not self.scenario.topic.strip():
            issues.append("話題が空です。")
        if self.scenario.material_kind not in MATERIAL_KINDS:
            issues.append(f"素材の種別が未知です: {self.scenario.material_kind!r}")
        refs = [c.actor_ref for c in self.cast]
        if len(refs) != len(set(refs)):
            issues.append("同じ配役が重複しています。")
        if len({c.persona_id for c in self.cast}) != len(self.cast):
            issues.append("同じ人格を二役に配していますが、v0.1では対応しません。")
        if self.context_options.category_selection not in (
            SELECTION_MODEL, SELECTION_MANUAL, SELECTION_NONE
        ):
            issues.append(
                f"カテゴリの選び方が未知です: {self.context_options.category_selection!r}"
            )
        return tuple(issues)

    def to_json(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "scheme_id": self.scheme_id,
            "scheme_version": self.scheme_version,
            "role_digest": self.role_digest,
            "role_characters": len(self.role_text),
            "cast": [c.to_json() for c in self.cast],
            "scenario": self.scenario.to_json(),
            "model_settings": self.model_settings.to_json(),
            "context_options": self.context_options.to_json(),
            "scheme_input": dict(self.scheme_input),
        }


@dataclass(frozen=True)
class Utterance:
    """共通表示用の1発話。作品固有の属性はここに入れない。"""

    utterance_id: str
    actor_ref: str
    text: str

    def to_json(self) -> dict[str, Any]:
        return {
            "utterance_id": self.utterance_id,
            "actor_ref": self.actor_ref,
            "text": self.text,
        }


@dataclass
class StageRecord:
    """1回のモデル呼び出しの記録。送ったものと返ったものを両方残す。"""

    stage_id: str
    model_id: str
    messages: list[dict[str, str]]
    request_characters: int
    schema_name: str = ""
    response: Any = None
    response_characters: int | None = None
    usage: dict[str, Any] | None = None
    started_at: str = ""
    finished_at: str = ""
    error: str | None = None

    def to_json(self) -> dict[str, Any]:
        return {
            "stage_id": self.stage_id,
            "model_id": self.model_id,
            "schema_name": self.schema_name,
            "request_characters": self.request_characters,
            "response_characters": self.response_characters,
            "usage": self.usage,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "error": self.error,
            "messages": [dict(m) for m in self.messages],
            "response": self.response,
        }


@dataclass
class SchemeOutcome:
    """作品adapterが返すもの。"""

    utterances: tuple[Utterance, ...] = ()
    scheme_output: dict[str, Any] = field(default_factory=dict)
    warnings: tuple[str, ...] = ()


@dataclass
class RehearsalResult:
    run_id: str
    request: RehearsalRequest
    status: str
    utterances: tuple[Utterance, ...] = ()
    scheme_output: dict[str, Any] = field(default_factory=dict)
    persona_contexts: tuple[PersonaContext, ...] = ()
    stages: list[StageRecord] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    started_at: str = ""
    finished_at: str = ""

    @property
    def succeeded(self) -> bool:
        return self.status == STATUS_SUCCESS

    def to_json(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "status": self.status,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "request": self.request.to_json(),
            "persona_contexts": [c.summary() for c in self.persona_contexts],
            "utterances": [u.to_json() for u in self.utterances],
            "scheme_output": self.scheme_output,
            "stages": [s.to_json() for s in self.stages],
            "warnings": list(self.warnings),
            "errors": list(self.errors),
        }


class ModelCall(Protocol):
    """runner が adapter へ渡す呼び出し口。記録とキャンセルは runner が持つ。"""

    def __call__(
        self,
        stage_id: str,
        messages: list[dict[str, str]],
        *,
        schema_name: str = "",
        schema: dict[str, Any] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> Any: ...


class SchemeAdapter(Protocol):
    """作品ごとの構成作家との接続。"""

    scheme_id: str
    scheme_version: int
    default_role_text: str

    def validate(self, request: RehearsalRequest) -> tuple[str, ...]:
        """作品固有の入力検査。問題があれば理由を返す。"""

    def run(
        self,
        request: RehearsalRequest,
        contexts: tuple[PersonaContext, ...],
        call: ModelCall,
    ) -> SchemeOutcome:
        """段階を進めて結果を返す。モデル呼び出しは必ず `call` を通す。"""


class LanguageModelClient(Protocol):
    """PPRが必要とする最小のprovider接続。"""

    provider_id: str

    def chat_json(
        self,
        model_id: str,
        messages: list[dict[str, str]],
        temperature: float,
        max_tokens: int,
        *,
        schema_name: str,
        schema: dict[str, Any],
    ) -> Any: ...
