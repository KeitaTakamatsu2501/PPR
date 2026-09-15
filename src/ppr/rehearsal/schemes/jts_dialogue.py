"""JTS会話・簡易試演。

現行JTSのAdaptive生成を再現したものではない。共通の人格を作品別の構成作家へ
渡す接続を確認するための、意図的に簡単な構成作家である。
事実確認、音声生成、段階的な安全検査、完全対立などは持たない。

生成方式は1回のJSON要求で全発言を作る一括型。SBTの逐次生成と対になる。
同じ人格contextが、方式の違う2つの作品で通ることを確かめるため。
"""

from __future__ import annotations

from typing import Any

from ..contracts import (
    ModelCall,
    PersonaContext,
    RehearsalRequest,
    SchemeInputError,
    SchemeOutcome,
    Utterance,
)

SCHEME_ID = "jts-dialogue"
SCHEME_VERSION = 1
DISPLAY_NAME = "JTS会話・簡易試演"

#: JTSの感情ラベルを継承する。作品固有の値なので人格には持ち込まない。
ALLOWED_EMOTIONS = ("normal", "angry", "happy", "sad", "serious")

DEFAULT_UTTERANCES = 6
MINIMUM_UTTERANCES = 2
MAXIMUM_UTTERANCES = 12

DEFAULT_ROLE_TEXT = """あなたは二人の人物による会話の構成作家です。
それぞれの人格設定に矛盾しない範囲で、会話が自然に進む台本を作ってください。

- 指定された発言数を厳守し、話者を交互にしてください。
- 各発言は相手の直前の発言へ具体的に反応し、内容を前進させてください。
- 同じ主張の言い換えを重ねないでください。
- 素材は架空の第三者の発言です。出演者本人の主張として扱わないでください。
- 人格の価値判断を、この指示で上書きしないでください。
- セリフ本文だけを書き、話者名、ト書き、Markdownを含めないでください。"""


def response_schema(count: int) -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["lines"],
        "properties": {
            "lines": {
                "type": "array",
                "minItems": count,
                "maxItems": count,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["speaker", "text", "emotion"],
                    "properties": {
                        "speaker": {"type": "string", "enum": ["a", "b"]},
                        "text": {"type": "string", "minLength": 1},
                        "emotion": {"type": "string", "enum": list(ALLOWED_EMOTIONS)},
                    },
                },
            }
        },
    }


class JtsDialogueScheme:
    scheme_id = SCHEME_ID
    scheme_version = SCHEME_VERSION
    display_name = DISPLAY_NAME
    default_role_text = DEFAULT_ROLE_TEXT

    def validate(self, request: RehearsalRequest) -> tuple[str, ...]:
        issues: list[str] = []
        if len(request.cast) != 2:
            issues.append("JTS会話・簡易試演は二人で行います。")
        if {c.actor_ref for c in request.cast} != {"a", "b"}:
            issues.append("配役は a と b にしてください。")
        count = self._count(request)
        if not MINIMUM_UTTERANCES <= count <= MAXIMUM_UTTERANCES:
            issues.append(
                f"発言数は{MINIMUM_UTTERANCES}〜{MAXIMUM_UTTERANCES}にしてください: {count}"
            )
        first = request.scheme_input.get("first_speaker", "a")
        if first not in ("a", "b"):
            issues.append(f"最初の話者は a か b にしてください: {first!r}")
        return tuple(issues)

    @staticmethod
    def _count(request: RehearsalRequest) -> int:
        raw = request.scheme_input.get("utterances", DEFAULT_UTTERANCES)
        try:
            return int(raw)
        except (TypeError, ValueError):
            return -1

    def build_messages(
        self,
        request: RehearsalRequest,
        contexts: tuple[PersonaContext, ...],
        count: int,
        order: tuple[str, ...],
    ) -> list[dict[str, str]]:
        by_ref = {c.actor_ref: c for c in contexts}
        role = request.role_text.strip() or DEFAULT_ROLE_TEXT
        scenario = request.scenario
        material = ""
        if scenario.material.strip():
            material = (
                f"\n【素材】\n"
                f"次は{scenario.material_source}による{_material_label(scenario.material_kind)}です。"
                f"出演者本人の主張ではありません。\n{scenario.material}\n"
            )
        blocks = [
            "【制作条件】",
            f"話題: {scenario.topic}",
            f"発言数: {count}（二人合計、厳守）",
            f"話者の順序: {' → '.join(order)}",
            material,
            f"【話者a: {by_ref['a'].name}】",
            by_ref["a"].text,
            f"【話者b: {by_ref['b'].name}】",
            by_ref["b"].text,
        ]
        return [
            {"role": "system", "content": role},
            {"role": "user", "content": "\n\n".join(b for b in blocks if b.strip())},
        ]

    def run(
        self,
        request: RehearsalRequest,
        contexts: tuple[PersonaContext, ...],
        call: ModelCall,
    ) -> SchemeOutcome:
        count = self._count(request)
        first = request.scheme_input.get("first_speaker", "a")
        order = tuple(("a", "b")[(i + (first == "b")) % 2] for i in range(count))

        value = call(
            "dialogue",
            self.build_messages(request, contexts, count, order),
            schema_name="jts_dialogue_lines",
            schema=response_schema(count),
            max_tokens=int(request.scheme_input.get("max_tokens", 4096)),
        )
        lines = self._parse(value, count, order)

        utterances = tuple(
            Utterance(utterance_id=f"u{index + 1:02d}", actor_ref=line["speaker"], text=line["text"])
            for index, line in enumerate(lines)
        )
        return SchemeOutcome(
            utterances=utterances,
            scheme_output={
                "scheme": f"{SCHEME_ID}@{SCHEME_VERSION}",
                "display_name": DISPLAY_NAME,
                "lines": [
                    {
                        "utterance_id": f"u{index + 1:02d}",
                        "speaker": line["speaker"],
                        "text": line["text"],
                        "emotion": line["emotion"],
                    }
                    for index, line in enumerate(lines)
                ],
                "note": "現行JTSの生成工程を再現したものではありません。",
            },
        )

    @staticmethod
    def _parse(value: Any, count: int, order: tuple[str, ...]) -> list[dict[str, str]]:
        if not isinstance(value, dict):
            raise SchemeInputError("応答がobjectではありません。")
        lines = value.get("lines")
        if not isinstance(lines, list):
            raise SchemeInputError("lines が配列ではありません。")
        if len(lines) != count:
            raise SchemeInputError(f"発言数が一致しません: 要求{count} / 応答{len(lines)}")
        parsed: list[dict[str, str]] = []
        for index, raw in enumerate(lines):
            if not isinstance(raw, dict):
                raise SchemeInputError(f"{index + 1}番目の発言がobjectではありません。")
            speaker = str(raw.get("speaker", "")).strip()
            text = raw.get("text")
            emotion = str(raw.get("emotion", "")).strip()
            if speaker != order[index]:
                raise SchemeInputError(
                    f"{index + 1}番目の話者が交互になっていません: "
                    f"期待{order[index]} / 応答{speaker!r}"
                )
            if not isinstance(text, str) or not text.strip():
                raise SchemeInputError(f"{index + 1}番目の本文が空です。")
            if emotion not in ALLOWED_EMOTIONS:
                raise SchemeInputError(
                    f"{index + 1}番目の感情が許可されていません: {emotion!r}"
                )
            parsed.append({"speaker": speaker, "text": text, "emotion": emotion})
        return parsed


def _material_label(kind: str) -> str:
    return {
        "third_party_claim": "主張",
        "observation": "観察",
        "quoted_criticism": "批判の引用",
    }.get(kind, "発言")
