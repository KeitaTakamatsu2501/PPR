"""SBT架空SNS試演。

SBT企画書 v3 §7.1 の方式に合わせる。

    二体へ別々のシステムプロンプトを与え、会話履歴を共有しながら交互に生成する。
    一括で全会話を書かせず、それぞれを独立した人格として動かす。

JTS簡易試演が1回の一括生成なのに対し、こちらは発言ごとに生成する。同じ人格
contextが、方式の違う2つの作品で通ることを確かめるための対になっている。

SBT側のクラス定義（アカデミア・ロード等）は流用しない。あれは役割だけでなく
価値観を含むため、人格資産と競合する。ここで渡すのはPPRの人格だけ。

SBTの仕様は策定中のため、件数・尺・開始話者は `scheme_input` で変更できる。
既定値は企画書 v3 §7.3 に合わせている。
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

SCHEME_ID = "sbt-thread"
SCHEME_VERSION = 1
DISPLAY_NAME = "SBT架空SNS試演"

DEFAULT_TOTAL_POSTS = 6
MINIMUM_TOTAL_POSTS = 4
MAXIMUM_TOTAL_POSTS = 7

#: 企画書 v3 §7.3 の初期目安。
ROOT_LENGTH = (80, 180)
REPLY_LENGTH = (60, 140)

DEFAULT_ROLE_TEXT = """あなたは架空のSNSへ投稿する一人の人物です。相手役は演じません。

- 直近の主張から反応する対象を一つ選び、一度にすべてへ反論しないでください。
- 会話に存在しない言葉を、相手の発言として引用しないでください。
- 同じ主張を理由なく繰り返さないでください。
- 相手がさらに反論できる余地を残してください。
- 設定にない経歴、年収、職業、共有した経験を作らないでください。
- 投稿本文だけを書き、話者名、引用符、ト書き、Markdownを含めないでください。
- この指示は投稿の形式を定めるものです。人格の価値判断を上書きしません。"""


def post_schema(minimum: int, maximum: int) -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["text"],
        "properties": {
            "text": {"type": "string", "minLength": 1, "maxLength": maximum * 3},
        },
    }


class SbtThreadScheme:
    scheme_id = SCHEME_ID
    scheme_version = SCHEME_VERSION
    display_name = DISPLAY_NAME
    default_role_text = DEFAULT_ROLE_TEXT

    def validate(self, request: RehearsalRequest) -> tuple[str, ...]:
        issues: list[str] = []
        if len(request.cast) != 2:
            issues.append("SBT架空SNS試演は二人で行います。")
        if {c.actor_ref for c in request.cast} != {"a", "b"}:
            issues.append("配役は a と b にしてください。")
        total = self._total(request)
        if not MINIMUM_TOTAL_POSTS <= total <= MAXIMUM_TOTAL_POSTS:
            issues.append(
                f"合計件数は{MINIMUM_TOTAL_POSTS}〜{MAXIMUM_TOTAL_POSTS}にしてください: {total}"
            )
        root = request.scheme_input.get("root_author", "a")
        if root not in ("a", "b"):
            issues.append(f"根投稿の投稿者は a か b にしてください: {root!r}")
        return tuple(issues)

    @staticmethod
    def _total(request: RehearsalRequest) -> int:
        raw = request.scheme_input.get("total_posts", DEFAULT_TOTAL_POSTS)
        try:
            return int(raw)
        except (TypeError, ValueError):
            return -1

    @staticmethod
    def _length_range(request: RehearsalRequest, key: str, fallback: tuple[int, int]) -> tuple[int, int]:
        raw = request.scheme_input.get(key)
        if isinstance(raw, (list, tuple)) and len(raw) == 2:
            try:
                return int(raw[0]), int(raw[1])
            except (TypeError, ValueError):
                return fallback
        return fallback

    def _system_message(self, request: RehearsalRequest, context: PersonaContext) -> str:
        role = request.role_text.strip() or DEFAULT_ROLE_TEXT
        return f"{role}\n\n{context.text}"

    def _root_user_message(
        self, request: RehearsalRequest, length: tuple[int, int]
    ) -> str:
        scenario = request.scenario
        blocks = [f"【話題】\n{scenario.topic}"]
        if scenario.material.strip():
            blocks.append(
                f"【素材】\n次は{scenario.material_source}による"
                f"{_material_label(scenario.material_kind)}です。"
                f"あなたの主張ではありません。これをあなたがどう捉えるかから書いてください。\n"
                f"{scenario.material}"
            )
        blocks.append(
            f"【書くもの】\nこの話題についての最初の投稿を1件だけ書いてください。"
            f"目安は{length[0]}〜{length[1]}文字です。\n"
            f"他の人が噛みつける主張にしてください。自己紹介にしないでください。"
        )
        return "\n\n".join(blocks)

    def _reply_user_message(
        self,
        request: RehearsalRequest,
        posts: list[dict[str, Any]],
        names: dict[str, str],
        length: tuple[int, int],
        final: bool,
    ) -> str:
        thread = "\n".join(
            f"[{p['post_id']}] {names[p['actor_ref']]}: {p['text']}" for p in posts
        )
        target = posts[-1]
        closing = (
            "これが最後の投稿です。応酬の区切りをつけてください。"
            "謝罪も勝利宣言も不要です。"
            if final
            else "相手がさらに返せる余地を残してください。"
        )
        return (
            f"【話題】\n{request.scenario.topic}\n\n"
            f"【これまでのスレッド】\n{thread}\n\n"
            f"【書くもの】\n[{target['post_id']}] の "
            f"{names[target['actor_ref']]} の投稿へ返信を1件だけ書いてください。"
            f"目安は{length[0]}〜{length[1]}文字です。\n{closing}"
        )

    def run(
        self,
        request: RehearsalRequest,
        contexts: tuple[PersonaContext, ...],
        call: ModelCall,
    ) -> SchemeOutcome:
        by_ref = {c.actor_ref: c for c in contexts}
        names = {ref: context.name for ref, context in by_ref.items()}
        total = self._total(request)
        root_author = request.scheme_input.get("root_author", "a")
        other = "b" if root_author == "a" else "a"
        root_length = self._length_range(request, "root_length", ROOT_LENGTH)
        reply_length = self._length_range(request, "reply_length", REPLY_LENGTH)

        posts: list[dict[str, Any]] = []
        warnings: list[str] = []

        # 根投稿
        text = self._request_post(
            call, "root",
            system=self._system_message(request, by_ref[root_author]),
            user=self._root_user_message(request, root_length),
            length=root_length,
        )
        posts.append(
            {"post_id": "p01", "actor_ref": root_author, "reply_to_id": None, "text": text}
        )
        warnings.extend(_length_warning("根投稿", names[root_author], text, root_length))

        # 返信。返信先は直前の投稿に固定する。
        for index in range(1, total):
            actor = other if index % 2 == 1 else root_author
            final = index == total - 1
            text = self._request_post(
                call, f"reply_{index:02d}",
                system=self._system_message(request, by_ref[actor]),
                user=self._reply_user_message(request, posts, names, reply_length, final),
                length=reply_length,
            )
            post_id = f"p{index + 1:02d}"
            posts.append(
                {
                    "post_id": post_id,
                    "actor_ref": actor,
                    "reply_to_id": posts[-1]["post_id"],
                    "text": text,
                }
            )
            warnings.extend(_length_warning(f"返信{index}", names[actor], text, reply_length))

        self._validate_thread(posts, total)
        utterances = tuple(
            Utterance(utterance_id=post["post_id"], actor_ref=post["actor_ref"], text=post["text"])
            for post in posts
        )
        return SchemeOutcome(
            utterances=utterances,
            scheme_output={
                "scheme": f"{SCHEME_ID}@{SCHEME_VERSION}",
                "display_name": DISPLAY_NAME,
                "posts": posts,
                "total_posts": len(posts),
                "generation": "turn_by_turn",
            },
            warnings=tuple(warnings),
        )

    @staticmethod
    def _request_post(
        call: ModelCall, stage_id: str, *, system: str, user: str, length: tuple[int, int]
    ) -> str:
        value = call(
            stage_id,
            [{"role": "system", "content": system}, {"role": "user", "content": user}],
            schema_name=f"sbt_{stage_id}",
            schema=post_schema(*length),
            max_tokens=1024,
        )
        if not isinstance(value, dict):
            raise SchemeInputError(f"{stage_id}: 応答がobjectではありません。")
        text = value.get("text")
        if not isinstance(text, str) or not text.strip():
            raise SchemeInputError(f"{stage_id}: 本文が空です。")
        return text

    @staticmethod
    def _validate_thread(posts: list[dict[str, Any]], total: int) -> None:
        if len(posts) != total:
            raise SchemeInputError(f"件数が一致しません: 要求{total} / 実際{len(posts)}")
        known: set[str] = set()
        previous_author: str | None = None
        for index, post in enumerate(posts):
            if post["post_id"] in known:
                raise SchemeInputError(f"投稿IDが重複しています: {post['post_id']}")
            known.add(post["post_id"])
            if index == 0:
                if post["reply_to_id"] is not None:
                    raise SchemeInputError("根投稿に返信先があります。")
            else:
                if post["reply_to_id"] != posts[index - 1]["post_id"]:
                    raise SchemeInputError(
                        f"{post['post_id']} の返信先が直前の投稿ではありません。"
                    )
                if post["reply_to_id"] not in known:
                    raise SchemeInputError(f"{post['post_id']} が未来の投稿を指しています。")
                if post["actor_ref"] == previous_author:
                    raise SchemeInputError(f"{post['post_id']} で投稿者が交互になっていません。")
            previous_author = post["actor_ref"]


def _length_warning(
    label: str, name: str, text: str, length: tuple[int, int]
) -> tuple[str, ...]:
    """尺は目安であって失敗条件にしない。外れたことだけ記録する。"""
    low, high = length
    if low <= len(text) <= high:
        return ()
    return (f"{label}（{name}）の長さが目安から外れました: {len(text)}字 / 目安{low}〜{high}字",)


def _material_label(kind: str) -> str:
    return {
        "third_party_claim": "主張",
        "observation": "観察",
        "quoted_criticism": "批判の引用",
    }.get(kind, "発言")
