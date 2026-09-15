"""通信しないprovider。配管と契約の検証に使う。

Fake の出力を実モデルの結果として記録しない。runには必ず provider が残る。
"""

from __future__ import annotations

from typing import Any, Callable

from ..rehearsal.contracts import RehearsalCancelled


class FakeLanguageModelClient:
    provider_id = "fake"

    def __init__(
        self,
        responses: dict[str, Any] | None = None,
        *,
        default: Any = None,
        on_call: Callable[[str, list[dict[str, str]]], None] | None = None,
    ) -> None:
        self.responses = dict(responses or {})
        self.default = default
        self.on_call = on_call
        self.calls: list[dict[str, Any]] = []

    def chat_json(
        self,
        model_id: str,
        messages: list[dict[str, str]],
        temperature: float,
        max_tokens: int,
        *,
        schema_name: str,
        schema: dict[str, Any],
    ) -> Any:
        self.calls.append(
            {
                "model_id": model_id,
                "schema_name": schema_name,
                "temperature": temperature,
                "max_tokens": max_tokens,
                "messages": [dict(m) for m in messages],
            }
        )
        if self.on_call is not None:
            self.on_call(schema_name, messages)
        if schema_name in self.responses:
            value = self.responses[schema_name]
            if isinstance(value, Exception):
                raise value
            if callable(value):
                return value(messages)
            return value
        if self.default is not None:
            return self.default
        raise KeyError(f"Fake providerに {schema_name!r} の応答が登録されていません")


class CancellingClient(FakeLanguageModelClient):
    """指定回数目の呼び出しで中止する。キャンセル経路の検証用。"""

    def __init__(self, *args: Any, cancel_at: int = 1, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.cancel_at = cancel_at

    def chat_json(self, *args: Any, **kwargs: Any) -> Any:
        if len(self.calls) + 1 >= self.cancel_at:
            self.calls.append({"cancelled": True})
            raise RehearsalCancelled("Fakeが中止しました")
        return super().chat_json(*args, **kwargs)
