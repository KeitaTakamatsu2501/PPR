#!/usr/bin/env python3
"""Phase 0 / Probe B — JTSの高品質オフライン生成を、PPR由来の人格で実行して接続契約を実測する。

目的は台本の品質評価ではない。構成作家(層2)が人格(層1)へ何を要求するかを、
実際のリクエストを全件記録して確定させること。

JTSのソースは変更しない。実行対象は phase0/jts_snapshot/ の抽出コピーのみ。

使い方:
  # 通信なし。プロンプト構築だけを確認する（配管の検証用）
  python3 probe_b_offline_generation.py --fake

  # LM Studio で実行（Mac 上で LM Studio を起動しておく）
  python3 probe_b_offline_generation.py --lm-studio
  python3 probe_b_offline_generation.py --lm-studio --model-contains gemma --max-requests 12

出力: phase0/out/probe_b_<timestamp>/
  requests.jsonl    各LLM要求と応答の全文
  summary.json      要求回数、段階、人格の寄与サイズ
  plan.json         成功時の OfflinePackagePlan
  speaker_a.txt / speaker_b.txt   構成作家へ渡した人格描画そのもの
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime as _dt
import json
import sys
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
SNAPSHOT = HERE / "jts_snapshot"
sys.path.insert(0, str(SNAPSHOT))

from character_addressing import resolve_pair_addressing_contract  # noqa: E402
from character_profiles import load_character_store  # noqa: E402
from dual_voice_chat_core import (  # noqa: E402
    ConversationConfig,
    SpeakerConfig,
    VoiceProfile,
)
from high_quality_character_profiles import (  # noqa: E402
    compose_high_quality_character_rules,
    load_high_quality_character_store,
    profiles_for_characters,
)
from offline_generation import (  # noqa: E402
    HIGH_QUALITY_CONTEXT_LIMIT,
    HIGH_QUALITY_GEMMA_4_31B_CONTEXT_LIMIT,
    AdaptiveGenerationOptions,
    AdaptiveHighQualityOfflineScriptGenerator,
    HighQualityOfflineScriptGenerator,
    _adaptive_compact_persona_source,
)

# 資産化の対象8人。ヒロアキ(kugimiya-e03d64755b)はJTS特化のため除外。
ASSET_IDS = (
    "hayamin-2548d9d189",
    "kikuko-de58d3efda",
    "eyja-9ca78ddb88",
    "muelsyse-dec1162707",
    "h-tsubasa-db3d1d5f64",
    "elmirea-ab93505eb7",
    "luciela-06fbc1148f",
    "ione-47f2d24ffb",
)

# IMPLEMENTATION_PLAN §8 の初期試演素材1。人物名も正解も含まない架空の題材。
DEFAULT_TOPIC = (
    "架空の町で、湿地を埋めて道路を延ばすと通勤が15分短くなる。"
    "「反対する人は町の成長を邪魔している」という主張をどう見るか。"
)


class ProbeLimitReached(RuntimeError):
    """記録上限に達したので意図的に停止した。"""


class RecordingClient:
    """LMクライアントを包んで、全要求と応答を記録する。未知の属性は委譲する。"""

    def __init__(self, delegate, out_dir: Path, max_requests: int | None):
        self._delegate = delegate
        self._log = (out_dir / "requests.jsonl").open("w", encoding="utf-8")
        self._max = max_requests
        self.records: list[dict] = []

    def __getattr__(self, name):  # provider_id, list_models など
        return getattr(self._delegate, name)

    def _record(self, method, model_id, messages, temperature, max_tokens, extra, result, error):
        entry = {
            "seq": len(self.records) + 1,
            "method": method,
            "model_id": model_id,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "extra": extra,
            "messages": [dict(m) for m in messages],
            "request_chars": sum(len(str(m.get("content", ""))) for m in messages),
            "response": result if isinstance(result, str) else json.loads(json.dumps(result, ensure_ascii=False, default=str)),
            "response_chars": len(result) if isinstance(result, str) else None,
            "error": error,
            "at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        }
        self.records.append(entry)
        self._log.write(json.dumps(entry, ensure_ascii=False) + "\n")
        self._log.flush()
        n = len(self.records)
        head = str(messages[0].get("content", ""))[:60].replace("\n", " ") if messages else ""
        print(f"  [{n:>2}] {method:<9} req={entry['request_chars']:>7}字  "
              f"resp={entry['response_chars'] if entry['response_chars'] is not None else '-':>6}  {head}…")
        if self._max is not None and n >= self._max:
            raise ProbeLimitReached(f"記録上限 {self._max} 件に到達したため停止しました。")

    def chat(self, model_id, messages, temperature, max_tokens):
        try:
            result = self._delegate.chat(model_id, messages, temperature, max_tokens)
        except Exception as exc:
            self._record("chat", model_id, messages, temperature, max_tokens, None, None, repr(exc))
            raise
        self._record("chat", model_id, messages, temperature, max_tokens, None, result, None)
        return result

    def chat_json(self, model_id, messages, temperature, max_tokens, *, schema_name, schema):
        extra = {"schema_name": schema_name, "schema_keys": sorted(schema.get("properties", {}))}
        try:
            result = self._delegate.chat_json(
                model_id, messages, temperature, max_tokens,
                schema_name=schema_name, schema=schema,
            )
        except Exception as exc:
            self._record("chat_json", model_id, messages, temperature, max_tokens, extra, None, repr(exc))
            raise
        self._record("chat_json", model_id, messages, temperature, max_tokens, extra, result, None)
        return result

    def close(self):
        self._log.close()


class FakeClient:
    """通信しない。プロンプト構築だけを確認するための固定応答。"""

    provider_id = "fake"
    is_remote = False
    single_model_fact_check_supported = False

    def chat(self, model_id, messages, temperature, max_tokens):
        return "（Fake応答：構成案のプレースホルダ）"

    def chat_json(self, model_id, messages, temperature, max_tokens, *, schema_name, schema):
        return {}


def stub_voice(character) -> VoiceProfile:
    """音声資産に触れずに SpeakerConfig を満たすためのスタブ。

    生成器本体(offline_generation 6568-8138行)は音声ファイルI/Oを行わないため、
    パスは解決されないままでよい。台本生成のみを対象とする。
    """
    return VoiceProfile(
        profile_id=character.voice_profile_id,
        experiment="phase0-probe",
        version="stub",
        gpt_weight=Path("/nonexistent/stub.ckpt"),
        sovits_weight=Path("/nonexistent/stub.pth"),
        reference_audio=Path(character.reference_audio),
        reference_text=character.reference_text,
        gpt_epoch=0, sovits_epoch=0, sovits_step=0,
    )


def build_speaker(key, character, profile, partner, partner_profile, model_id, temperature):
    """JTS実運用(high_quality_generation_gui.py:2568 / service_run_resolver.py:500)と同一の組み立て。"""
    return SpeakerConfig(
        key=key,
        name=character.name,
        personality=compose_high_quality_character_rules(
            character, profile,
            include_topic_stances=False,
            person_target_character_id=partner.character_id,
        ),
        model_id=model_id,
        temperature=temperature,
        voice=stub_voice(character),
        reference_audio=Path(character.reference_audio),
        reference_text=character.reference_text,
        voice_volume_percent=character.voice_volume_percent,
        pan_position="left" if key == "a" else "right",
        pan_strength=70.0,
        fragment_interval=0.1,
        voice_references=character.voice_references_with_primary(
            Path(character.reference_audio), character.reference_text
        ),
        topic_stances=profile.topic_stances,
        addressing_contract=resolve_pair_addressing_contract(character, profile, partner),
    )


def resolve_lm_studio(url, model_contains):
    from dual_voice_chat_core import LMStudioClient

    client = LMStudioClient(base_url=url)
    models = client.list_models()
    if not models:
        raise SystemExit(f"LM Studio にモデルが見つかりません: {url}")
    print(f"LM Studio のモデル一覧 ({url}):")
    for m in models:
        print(f"  - key={m.model_id!r} loaded={m.loaded} instances={m.instance_ids} ctx={m.context_length}")
    needle = model_contains.lower()
    picked = [m for m in models if needle in m.model_id.lower()] or models
    loaded = [m for m in picked if m.loaded] or picked
    chosen = loaded[0]
    # model key と 推論instance ID は別物。送信には inference_id を使う。
    client.model_keys_by_id[chosen.inference_id] = chosen.model_id
    print(f"\n選択: key={chosen.model_id!r} / inference_id={chosen.inference_id!r} / ctx={chosen.context_length}")
    return client, chosen


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fake", action="store_true", help="通信せずプロンプト構築だけ確認")
    ap.add_argument("--lm-studio", action="store_true", help="LM Studio で実行")
    ap.add_argument("--url", default="http://127.0.0.1:1234")
    ap.add_argument("--model-contains", default="gemma")
    ap.add_argument("--a", default="h-tsubasa-db3d1d5f64", help="話者AのキャラクターID")
    ap.add_argument("--b", default="elmirea-ab93505eb7", help="話者BのキャラクターID")
    ap.add_argument("--topic", default=DEFAULT_TOPIC)
    ap.add_argument("--turns", type=int, default=6)
    ap.add_argument("--temperature", type=float, default=0.7)
    ap.add_argument("--response-tokens", type=int, default=4096)
    ap.add_argument("--context-limit", type=int, default=HIGH_QUALITY_GEMMA_4_31B_CONTEXT_LIMIT,
                    help=f"32k={HIGH_QUALITY_GEMMA_4_31B_CONTEXT_LIMIT} / 128k={HIGH_QUALITY_CONTEXT_LIMIT} のみ")
    ap.add_argument("--max-requests", type=int, default=None, help="このLLM要求数で意図的に停止する")
    ap.add_argument("--ignore-context-mismatch", action="store_true",
                    help="モデルの実コンテキストが不足していても続行する")
    ap.add_argument("--adaptive", action="store_true",
                    help="Adaptive経路を使う。context_limit<=32kなら人格は4000字カプセルへ自動圧縮される")
    args = ap.parse_args()
    if not (args.fake or args.lm_studio):
        ap.error("--fake か --lm-studio のどちらかを指定してください")
    for cid in (args.a, args.b):
        if cid not in ASSET_IDS:
            ap.error(f"{cid} は資産化対象8人に含まれません: {ASSET_IDS}")

    stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    out_dir = HERE / "out" / f"probe_b_{stamp}"
    out_dir.mkdir(parents=True, exist_ok=True)

    characters = {c.character_id: c for c in load_character_store()}
    profiles = {
        p.character_id: p
        for p in profiles_for_characters(list(characters.values()), load_high_quality_character_store())
    }

    if args.lm_studio:
        delegate, model = resolve_lm_studio(args.url, args.model_contains)
        model_id = model.inference_id
        # JTSの _ensure_request_fits は config.context_limit だけを見ており、
        # providerが報告する実際の context_length を検査しない。ここで先に突き合わせる。
        actual = model.context_length
        if actual is not None and actual < args.context_limit:
            print(
                f"\n[警告] モデルの実コンテキスト {actual} < 宣言した context_limit "
                f"{args.context_limit}\n"
                f"        LM Studio でこのモデルを {args.context_limit} 以上の"
                f"コンテキスト長でロードし直してください。\n"
                f"        このまま続けると、後段のJSON要求が黙って切り詰められて失敗します。"
            )
            if not args.ignore_context_mismatch:
                raise SystemExit("中断しました（--ignore-context-mismatch で強行できます）")
        model_label = {"key": model.model_id, "inference_id": model.inference_id,
                       "context_length": model.context_length}
    else:
        delegate, model_id = FakeClient(), "fake-model"
        model_label = {"key": "fake", "inference_id": "fake-model", "context_length": None}

    ca, cb = characters[args.a], characters[args.b]
    pa, pb = profiles[args.a], profiles[args.b]
    speaker_a = build_speaker("a", ca, pa, cb, pb, model_id, args.temperature)
    speaker_b = build_speaker("b", cb, pb, ca, pa, model_id, args.temperature)
    (out_dir / "speaker_a.txt").write_text(speaker_a.personality, encoding="utf-8")
    (out_dir / "speaker_b.txt").write_text(speaker_b.personality, encoding="utf-8")

    config = ConversationConfig(
        speaker_a=speaker_a,
        speaker_b=speaker_b,
        common_rules=(
            "二人は日本語で自然に会話し、相手の直前の発言へ具体的に反応してください。"
            "発言ごとに内容を前進させ、同じ説明や結論を言い換えて繰り返さないでください。"
        ),
        topics=(args.topic,),
        context_limit=args.context_limit,
        response_tokens=args.response_tokens,
        save_audio=False,          # 音声合成は行わない
        turns_per_topic=args.turns,
        first_speaker="a",
        stereo_positioning=False,
    )

    # 32kコンテキストでAdaptive経路が実際に使う人格カプセルを記録しておく。
    capsules = {}
    for key, sp, other in (("a", speaker_a, speaker_b), ("b", speaker_b, speaker_a)):
        try:
            cap = _adaptive_compact_persona_source(sp, other.name)
        except Exception as exc:  # noqa: BLE001
            cap = f"<<カプセル生成に失敗: {type(exc).__name__}: {exc}>>"
        capsules[key] = cap
        (out_dir / f"capsule_{key}.txt").write_text(cap, encoding="utf-8")
        print(f"  人格カプセル {key.upper()}: 原典 {len(sp.personality)}字 -> {len(cap)}字")

    client = RecordingClient(delegate, out_dir, args.max_requests)
    generator_cls = (
        AdaptiveHighQualityOfflineScriptGenerator
        if args.adaptive
        else HighQualityOfflineScriptGenerator
    )
    generator = generator_cls(client, on_status=lambda m: print(f"  · {m}"))

    print(f"\n話者A: {ca.name} ({args.a})   話者B: {cb.name} ({args.b})")
    print(f"人格描画: A={len(speaker_a.personality)}字 / B={len(speaker_b.personality)}字")
    print(f"トピック: {args.topic[:60]}…")
    print(f"context_limit={args.context_limit} turns={args.turns} "
          f"route={'adaptive' if args.adaptive else 'high-quality'}\n")

    status, plan, error = "success", None, None
    try:
        if args.adaptive:
            # Adaptive の generate は index が位置引数で、options を受け取る。
            plan = generator.generate(0, config, args.topic, AdaptiveGenerationOptions())
        else:
            plan = generator.generate(index=0, config=config, topic=args.topic)
    except ProbeLimitReached as exc:
        status, error = "stopped_by_probe_limit", str(exc)
        print(f"\n{exc}")
    except Exception as exc:  # noqa: BLE001 — 何で落ちたかも実測結果
        status, error = "failed", f"{type(exc).__name__}: {exc}"
        (out_dir / "traceback.txt").write_text(traceback.format_exc(), encoding="utf-8")
        print(f"\n生成が中断しました: {status} / {error}")
    finally:
        client.close()

    if plan is not None:
        try:
            payload = dataclasses.asdict(plan) if dataclasses.is_dataclass(plan) else plan
        except Exception:
            payload = repr(plan)
        (out_dir / "plan.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
        )

    summary = {
        "status": status,
        "error": error,
        "mode": "lm-studio" if args.lm_studio else "fake",
        "route": "adaptive" if args.adaptive else "high-quality",
        "persona_capsules": {k: {"chars": len(v)} for k, v in capsules.items()},
        "model": model_label,
        "speaker_a": {"id": args.a, "name": ca.name,
                      "persona_chars": len(speaker_a.personality),
                      "topic_stances": len(pa.topic_stances),
                      "person_stances_total": len(pa.person_stances)},
        "speaker_b": {"id": args.b, "name": cb.name,
                      "persona_chars": len(speaker_b.personality),
                      "topic_stances": len(pb.topic_stances),
                      "person_stances_total": len(pb.person_stances)},
        "topic": args.topic,
        "turns_per_topic": args.turns,
        "context_limit": args.context_limit,
        "request_count": len(client.records),
        "requests": [
            {"seq": r["seq"], "method": r["method"], "schema": (r["extra"] or {}).get("schema_name"),
             "request_chars": r["request_chars"], "response_chars": r["response_chars"],
             "error": r["error"]}
            for r in client.records
        ],
    }
    (out_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"\n状態: {status} / LLM要求 {len(client.records)} 件")
    print(f"出力: {out_dir}")
    return 0 if status in ("success", "stopped_by_probe_limit") else 1


if __name__ == "__main__":
    raise SystemExit(main())
