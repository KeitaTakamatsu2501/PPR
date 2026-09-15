"""LM Studio への接続。

model key と 推論instance ID は別物として扱う。GUIの選択はmodel keyを保持し、
送信には inference_id を使う。ログには両方を残す（IMPLEMENTATION_PLAN §6.1）。

通常起動ではこのモジュールをimportしない。明示的な試演操作のときだけ読み込む。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

DEFAULT_BASE_URL = "http://127.0.0.1:1234"
CONNECT_TIMEOUT = 5
READ_TIMEOUT = 600


class LmStudioError(RuntimeError):
    pass


@dataclass(frozen=True)
class ModelInfo:
    model_key: str
    inference_id: str
    loaded: bool = False
    context_length: int | None = None
    provider: str = "lm-studio"

    @property
    def display_name(self) -> str:
        suffix = "  [ロード済み]" if self.loaded else ""
        context = f"  ctx={self.context_length:,}" if self.context_length else ""
        return f"{self.model_key}{context}{suffix}"


class LmStudioClient:
    provider_id = "lm-studio"

    def __init__(self, base_url: str = DEFAULT_BASE_URL) -> None:
        self.base_url = base_url.rstrip("/")
        self.model_keys_by_inference_id: dict[str, str] = {}

    def _session(self):
        import requests

        session = requests.Session()
        # ローカル接続。プロキシを経由させない。
        session.trust_env = False
        return session

    def list_models(self) -> list[ModelInfo]:
        import requests

        try:
            with self._session() as session:
                response = session.get(
                    f"{self.base_url}/api/v1/models", timeout=(CONNECT_TIMEOUT, 30)
                )
                response.raise_for_status()
                payload = response.json()
        except requests.RequestException as exc:
            raise LmStudioError(f"LM Studio へ接続できません（{self.base_url}）: {exc}") from exc

        models: list[ModelInfo] = []
        for item in payload.get("models", []):
            if item.get("type") != "llm":
                continue
            model_key = item.get("key")
            if not model_key:
                continue
            instances = item.get("loaded_instances") or []
            instance_ids = tuple(str(i["id"]) for i in instances if i.get("id"))
            config = instances[0].get("config", {}) if instances else {}
            raw_context = config.get("context_length")
            inference_id = instance_ids[0] if instance_ids else str(model_key)
            self.model_keys_by_inference_id[inference_id] = str(model_key)
            models.append(
                ModelInfo(
                    model_key=str(model_key),
                    inference_id=inference_id,
                    loaded=bool(instance_ids),
                    context_length=int(raw_context) if raw_context else None,
                )
            )
        return models

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
        import requests

        body = {
            "model": model_id,
            "messages": [dict(m) for m in messages],
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
        }
        if schema:
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": schema_name or "response", "strict": True, "schema": schema},
            }
        try:
            with self._session() as session:
                response = session.post(
                    f"{self.base_url}/v1/chat/completions",
                    json=body,
                    timeout=(CONNECT_TIMEOUT, READ_TIMEOUT),
                )
        except requests.RequestException as exc:
            # 送信済みかどうかが曖昧な失敗は自動再送しない。作者の明示的な再実行へ戻す。
            raise LmStudioError(f"{schema_name}: 送信に失敗しました: {exc}") from exc

        if response.status_code >= 400:
            raise LmStudioError(
                f"{schema_name}: HTTP {response.status_code}: {response.text[:400]}"
            )
        payload = response.json()
        choices = payload.get("choices") or []
        if not choices:
            raise LmStudioError(f"{schema_name}: 応答が空です。")
        content = choices[0].get("message", {}).get("content")
        if not isinstance(content, str) or not content.strip():
            raise LmStudioError(f"{schema_name}: 本文が空です。")
        text = content.strip()
        if text.startswith("```"):
            text = text.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        try:
            return json.loads(text)
        except ValueError as exc:
            raise LmStudioError(
                f"{schema_name}: JSONとして解析できません: {text[:300]}"
            ) from exc
