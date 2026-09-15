# Phase 0 実測結果 — 人格と構成作家の接続契約

実施日：2026-09-15。方法：JTSの高品質オフライン生成を、JTS無変更のまま実行して全要求を記録。

本書は実測の記録である。推測と実測を混ぜない。未実施の項目は未実施と書く。

## 1. 実施環境

- 実行対象は `phase0/jts_snapshot/` の抽出コピー（39ファイル、`EXTRACTION_MANIFEST.json` に全件SHA-256。抽出時点で全件ソース一致）。
- JTSのソース、人格データ、gitツリーは一切変更していない。`PYTHONDONTWRITEBYTECODE=1` で `__pycache__` も生成していない。
- `offline_generation` の推移的JTS依存は36モジュール。外部依存は `requests` のみ（`botocore` / `nltk` / `aws_bedrock_token_generator` の import は全て関数内の遅延importで、モジュール読込には不要）。
- 実LLM：LM Studio `google/gemma-4-31b-qat`、`context_length=33024`、`http://127.0.0.1:1234`。
- 実行：`AdaptiveHighQualityOfflineScriptGenerator.generate()`、32kコンパクト経路、6往復12発言。LLM要求32件で `status=success`。

## 2. 対象8人の実データ

ヒロアキ（`kugimiya-e03d64755b`）はJTS特化のため資産化対象から除外。

| | 概要 | 基本設定 | 口調 | セリフ例 | 4本文計 | 層3への描画 |
|---|---|---|---|---|---|---|
| ミラ `hayamin-2548d9d189` | 800 | 6,608 | 4,150 | 3,810 | 15,368 | 15,692 |
| ヴェルミリア `kikuko-de58d3efda` | 746 | 4,609 | 3,913 | 2,905 | 12,173 | 12,519 |
| 宙星リリカ `eyja-9ca78ddb88` | 723 | 3,160 | 1,938 | 2,270 | 8,091 | 8,481 |
| ラティア `muelsyse-dec1162707` | 700 | 5,074 | 1,801 | 3,803 | 11,378 | 11,739 |
| 久本礼司 `h-tsubasa-db3d1d5f64` | 747 | 4,259 | 7,920 | 5,790 | 18,716 | 19,061 |
| テルミナス `elmirea-ab93505eb7` | 619 | 3,032 | 2,505 | 2,120 | 8,276 | 8,633 |
| リュシエラ `luciela-06fbc1148f` | 814 | 4,772 | 4,148 | 3,595 | 13,329 | 13,674 |
| イオネ `ione-47f2d24ffb` | 925 | 3,184 | 1,303 | 644 | 6,056 | 6,402 |

- 全8人が `topic_stances` 195件（194テンプレート＋固有カテゴリ「恒常的な価値境界」）。ヒロアキのみ194件で「恒常的な価値境界」を持たない。
- 全8人が `person_stances` 8件。うち1件はヒロアキ向け。PPR内で解決可能な関係は56件、ヒロアキ向けの未解決参照が8件。
- `TOPIC_CATEGORIES` は厳密に194件で「恒常的な価値境界」を含まない。内訳は大分類22件＋小カテゴリ172件（小カテゴリは `/` を含む）。
- 4本文は平文ではなく `【...】` 見出しで階層化された構造化文書（ミラは本文内に約60見出し）。
- locale別 `enabled`：ja-JP は9件 true、en-US / ko-KR / zh-TW は全件 false。「enabled で対象を選ぶ」実装は他localeで0件になる。

## 3. 層の境界

`high_quality_character_profiles.py:717` の `compose_high_quality_character_rules()` が、人格データ（層1）を `SpeakerConfig.personality`（層3）へ変換する唯一の関数。`SpeakerConfig` の組み立てはJTS全体で2箇所のみ（`high_quality_generation_gui.py:2568`、`service_run_resolver.py:500`）。両者は同一の引数で呼ぶ。

付与される枠は約350字。内訳：

作品非依存
- `【固定プロフィール】` 名前 / 一人称 / 二人称 / 種族・存在種別 / 人類との関係
- `【キャラクター概要】` `【基本設定】` `【口調・話し方】` `【セリフのサンプル】`
- `【人物ごとの感情・関係性】`
- 「上記を設定資料ではなく本人の記憶・価値観として一貫して反映してください。」
- 「人物が会話に関係するときは、対応する関係性を優先してください。」

JTS固有（3箇所のみ）
- 見出し `【高品質モード専用キャラクター指示書：{name}】`
- 見出し `【高品質会話での適用】`
- 「読み上げに不要な話者名、ト書き、Markdown、設定の解説は返答へ含めないでください。」（TTS前提）

両呼び出し元とも `include_topic_stances=False`。194件のスタンスは文字列へ畳み込まず、`SpeakerConfig.topic_stances` として構造化データのまま構成作家へ渡る。畳み込むと描画は3〜4倍に膨れる（ミラ 15,692 → 42,568字）。

`person_stances` は `target_character_id` が今回の相手に一致する行だけが渡る。8件保持のうち1件。名前のみの旧行は全て残る。実装コメントに「同名の別人へ漏れてはならない」と明記。

## 4. 人格がプロンプトへ入る3経路（32kコンパクト時）

`compact_context = config.context_limit <= 32768` のとき、以下が適用される。

| 層1の要素 | 経路 | 実測 |
|---|---|---|
| 固定プロフィール / 概要 / 基本属性 / 声と基調 / 発話構造 / 避ける話し方 / セリフ例 / 相手への関係 | `_adaptive_compact_persona_source()` が原文の行・文を抽出 | 上限4,000字。久本礼司 19,068→2,469字、テルミナス 8,624→2,293字 |
| 恒常的な価値境界（1件） | `build_character_value_contract()` → `render_character_value_runtime_prompt()` | 原文を一字一句そのまま全文投入（久本礼司で約620字） |
| 小カテゴリ172件 | カテゴリ名の一覧のみをモデルへ提示 → モデルが関連を選択 → 選択分の原文だけ投入 | 1話者あたり最大4件。使用ラウンドは1と6のみ、`hard_persona_support` |
| 大分類22件 | `"large_category_scope": "omitted_for_compact_context"` | 未使用 |

3経路すべてが原文の引用で統一されており、LLMによる要約は使われていない。カプセル冒頭に「以下は保存済み原典の完全な行または文だけを選択した。未収録箇所を否定する資料ではない。」と明記される。

実行時に選ばれた小カテゴリ：
- 久本礼司：人類/人間の本性、繁殖/子育て、社会/都市、言語/沈黙
- テルミナス：技術/交通、海洋/海面上昇、食物/畜産、宇宙/人類の未来

これは `IMPLEMENTATION_PLAN.md` §7.2 が要求する「選択トピック」方式の実装である。相違点は、選ぶのが作者ではなくモデルであること。

## 5. 呼称契約

`character_addressing.py` の `resolve_pair_addressing_contract()` が、`humanity_membership` / `direct_address_override` / `perceived_humanity_override` からペア単位で導出する。プロンプトへは約700字が入る。単なる呼称ではなく、二人称の単複と人類境界の契約である。

- 単独の相手を指す二人称の固定
- 人類だけへ述べる場合は「人間たち」「人類」と明示し、相手を含む二人称に置き換えない
- 両方へ述べる場合は両者を明示して分ける
- 裸の複数二人称（「あなた方」「君たち」）は境界を曖昧にするため禁止
- 「この契約は呼称に必要な限定情報です。ここにない相手の人格・来歴・価値観を推測しないでください。」

作品非依存であり、SBTでも必要。現行 `IMPLEMENTATION_PLAN.md` §2 の抽出元リストに `character_addressing.py` は記載がない。

## 6. 実生成の結果

`google/gemma-4-31b-qat` @ 33,024 コンテキスト、LLM要求32件、所要約73分、`status=success`。

12発言、総1,909字、概算5.3分。品質警告2件（最低尺8.0分に未達）。これは尺の問題であり人格再現の問題ではない。

カプセル2,469字から生成された久本礼司の発話には、政治家の敬体、血語彙（「血流を適度に滞らせる」「制度的な凝固」「巨大な循環器」）、原典の核である「薄く長く苦しみ続ける社会」が現れた。カプセル2,293字から生成されたテルミナスの発話には、「わたくし」、静かな憎悪、動植物への慈愛（ミズゴケ、微生物、渡り鳥）が現れた。

**Phase 0 の問い「PPRの人格表現に、実運用級の構成作家が要求する情報が全部入っているか」への答えは「入っている」。必要量は原典の12〜27%だった。**

作者による人物らしさの判定（ACCEPTANCE A項目相当）は未実施。

## 7. 計画書へ反映すべき差分

1. `character_addressing.py` を抽出元へ追加。呼称契約を層3の必須部品とする。同様に `app_paths.py`、`service_lm_usage_context.py` も抽出元リストに未記載。`myfriends_compatibility.py` は `SOURCE_BASELINE.json` にすら無いがGUIがimportしている。
2. 「恒常的な価値境界」は194テンプレート外の固有カテゴリであることを明記する。現行 §3.3 は「194カテゴリの順序と文字列を保持する」までしか書いていない。
3. providerが報告する実コンテキスト長を検査する。JTSの `_ensure_request_fits` は `config.context_limit`（UI設定値）だけを見ており、`LMModel.context_length` を使っていない。本Phaseの失敗はこの穴による（モデルが8,192でロードされた状態で32,768を宣言し、3件目のJSONが黙って壊れた）。`IMPLEMENTATION_PLAN.md` §7.2 は「利用可能ならproviderのcontext上限を使う」と書いているが、JTS側は満たしていない。PPRで実装すべき差分。
4. JTSのトークン概算は日本語で約1.8トークン/字と保守的。実測ではgemmaトークナイザに対して約2倍の過大見積もり。
5. 音声パスは実データでは絶対パス（`/Users/keita/dev/private/JTS/charactors/...`）。§4.2 の「相対パスはGPT-SoVITS root基準」という記述は実態と合わない。
6. `reference_sets` は全件 `normal` のみ。感情別参照は実データに存在しない。
7. 未知フィールドは現時点でゼロ。fixed 16キー / personalities 7キー / one_to_one 3キー / detail 7キーは §4.2 の列挙と完全一致。
8. Adaptive経路に `hiroaki_fixed_opposite_conclusion` / `hiroaki_hard_opposition_required` / `hiroaki_forced_contest_override` というキャラID固有の分岐が存在する。§2.3 が「JTSの配役規則はJTS側演出として記録する」としていたものの実体。

## 8. 成果物

- `phase0/jts_snapshot/` — 抽出スナップショット39ファイルと `EXTRACTION_MANIFEST.json`
- `phase0/probe_a_persona_rendering.py` — 人格描画の分類（LLM不要）
- `phase0/probe_b_offline_generation.py` — 接続ハーネス。`--fake` / `--lm-studio`、`--adaptive`、`--max-requests`、コンテキスト不一致ガード
- `phase0/out/` — 全要求の全文記録、人格カプセル、生成された台本
