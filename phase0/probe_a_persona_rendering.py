"""Phase 0 / Probe A — 人格が構成作家へどう渡るかを、LLMを呼ばずに実測する。

JTSの `compose_high_quality_character_rules()` は、人格データ(層1)を
SpeakerConfig.personality(層3) へ変換する唯一の関数である。
本プローブはその出力を8人分レンダリングし、
  - 作品非依存の部分（PPRの人格資産が持つべきもの）
  - JTS固有の部分（作品ごとの構成作家に属するもの）
を分類するための実測データを出力する。

JTSのソースは読み取り専用。実行対象は phase0/jts_snapshot/ の抽出コピーのみ。
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

SNAPSHOT = Path(__file__).resolve().parent / "jts_snapshot"
sys.path.insert(0, str(SNAPSHOT))

from character_profiles import load_character_store  # noqa: E402
from high_quality_character_profiles import (  # noqa: E402
    compose_high_quality_character_rules,
    load_high_quality_character_store,
    profiles_for_characters,
)

# 資産化の対象。ヒロアキ(kugimiya-e03d64755b)はJTS特化のため除外。
TARGET_IDS = (
    "hayamin-2548d9d189",
    "kikuko-de58d3efda",
    "eyja-9ca78ddb88",
    "muelsyse-dec1162707",
    "h-tsubasa-db3d1d5f64",
    "elmirea-ab93505eb7",
    "luciela-06fbc1148f",
    "ione-47f2d24ffb",
)


def main() -> int:
    characters = {c.character_id: c for c in load_character_store()}
    profiles = {
        p.character_id: p
        for p in profiles_for_characters(
            list(characters.values()), load_high_quality_character_store()
        )
    }

    out_dir = Path(__file__).resolve().parent / "out"
    out_dir.mkdir(exist_ok=True)
    report: dict[str, object] = {"targets": [], "section_matrix": {}}

    for cid in TARGET_IDS:
        character = characters[cid]
        profile = profiles[cid]
        # 相手は「自分以外の最初の対象」。person_stances の絞り込み挙動を観測する。
        partner_id = next(p for p in TARGET_IDS if p != cid)

        # 実運用と同じ呼び方（GUI:2568 / service_run_resolver:500 と同一）
        rendered = compose_high_quality_character_rules(
            character,
            profile,
            include_topic_stances=False,
            person_target_character_id=partner_id,
        )
        # 比較用：トピックスタンスを畳み込んだ場合（既定値）
        rendered_with_topics = compose_high_quality_character_rules(
            character, profile, include_topic_stances=True,
            person_target_character_id=partner_id,
        )

        (out_dir / f"rendered_{cid}.txt").write_text(rendered, encoding="utf-8")

        sections = re.findall(r"^【([^】]+)】", rendered, re.M)
        report["targets"].append(
            {
                "character_id": cid,
                "name": character.name,
                "partner_id": partner_id,
                "rendered_chars": len(rendered),
                "rendered_chars_with_topic_stances": len(rendered_with_topics),
                "sections": sections,
                "person_lines_kept": rendered.count("\n- "),
                "topic_stances_total": len(profile.topic_stances),
                "person_stances_total": len(profile.person_stances),
            }
        )
        for s in sections:
            report["section_matrix"].setdefault(s, []).append(character.name)

    (out_dir / "probe_a_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print(f"{'name':<10}{'描画長':>8}{'+topics':>9}{'関係行':>7}  sections")
    for t in report["targets"]:
        print(
            f"{t['name']:<10}{t['rendered_chars']:>8}"
            f"{t['rendered_chars_with_topic_stances']:>9}"
            f"{t['person_lines_kept']:>7}  {'/'.join(t['sections'])}"
        )
    print("\n=== セクション出現マトリクス ===")
    for s, names in report["section_matrix"].items():
        print(f"  {s:<32} {len(names)}/8")
    print(f"\n出力: {out_dir}")
    return 0


if __name__ == "__main__":
    os.environ.setdefault("PYTHONDONTWRITEBYTECODE", "1")
    raise SystemExit(main())
