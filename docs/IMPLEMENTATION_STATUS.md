# 実装状況

最終更新：2026-09-15

進め方は [PHASE_PLAN.md](PHASE_PLAN.md)、接続契約の実測は [PHASE_0_FINDINGS.md](PHASE_0_FINDINGS.md) を参照。

| Phase | 状態 | 内容 |
|---|---|---|
| Phase 0 | **完了** | 接続契約の実地調査。JTS無変更のまま実台本を1本生成して確認 |
| Phase 1 | **完了** | 人格ビルダー。8人 / ja-JP の取込、編集、保存 |
| Phase 2 | **実装完了 / 作者確認待ち** | 三層の関節。`build_persona_context` と2作品のscheme |
| Phase 3 | 保留 | JTSへの反映。判断保留のまま凍結 |
| Phase 4 | 未着手 | 設定ワークショップ、海外locale |

## Phase 0（完了）

`phase0/` に調査ハーネスを残している。

- `jts_snapshot/` — 実行に必要な39ファイルの抽出コピー。`EXTRACTION_MANIFEST.json` に全件SHA-256
- `probe_a_persona_rendering.py` — 人格描画の分類（LLM不要）
- `probe_b_offline_generation.py` — 接続ハーネス。`--fake` / `--lm-studio`、`--adaptive`、
  `--max-requests`、コンテキスト不一致ガード
- `out/` — 全LLM要求の全文記録、人格カプセル、生成された台本

実行実績：`google/gemma-4-31b-qat` @ 33,024コンテキスト、Adaptive経路、LLM要求32件、
所要約73分、`status=success`。12発言の台本を生成。

## Phase 1（完了）

### 実装したもの

```
src/ppr/
  domain.py        人格データモデル。Tk/HTTP/JTS/音声に非依存
  storage.py       library.json の atomic 保存とプロセスロック
  revisions.py     canonical JSON からの revision_id
  sections.py      本文の【...】見出し分割。分割→結合が原文と完全一致
  config.py        データ置き場の解決（--data-dir / PPR_DATA_DIR）
  templates.py     同梱194カテゴリの読み込み
  assets.py        資産化対象8人の定義
  __main__.py      CLI（import-jts / list / check-drift）とGUI起動
  adapters/jts/importer.py   JTS一方向取込
  adapters/jts/restore.py    取込原本との突き合わせと復元
  rehearsal/capsule.py       人格カプセル（Phase 2-a、実装中）
  ui/state.py      編集状態。Tk非依存。EditTargetによる束縛つき書き込み
  ui/app.py        Tk編集画面
  resources/topic_templates/jts_194_v1.json
scripts/check.py   compile / ruff / unittest
tests/             108件
```

コード約3,300行。

### 検証済みの事項

実データ（JTS ja-JP）に対して：

- 8人 × 4本文が元JSONと文字単位で一致
- トピック195件の順序・カテゴリ名・本文が一致。大分類22 / 小カテゴリ172
- 全8人が「恒常的な価値境界」を保持
- 人物関係8件。全員がヒロアキ向けの未解決参照を1件持つ
- 呼称の例外・人類認識の例外が一致
- 取込の前後で元JTSのハッシュが不変
- 再取込しても同じ `revision_id`（`entry_id` を決定論的に導出）
- 通常importで JTS系・requests・botocore・socket が一切入らない

GUIはmacOS実機で確認済み（2026-09-15）。

### 実装中に見つけて直した不具合

再描画中のイベント抑止を真偽値1個で管理していたため、入れ子の描画処理が外側の抑止を
解除し、直前に表示していた人格の本文が新しい人格へ書き込まれていた。

対策は二重にした。

1. 抑止を深さカウンタ（contextmanager）へ変更
2. 編集中のウィジェットが「どの人格のどこ」を映しているかを `EditTarget` で束縛し、
   書き込み直前に現在の状態と照合する。一致しなければ書き込まない

`ui/state.py` はTk非依存なので、この種の事故はテストで捕まえられる（10件追加）。

### Phase 1 で作らなかったもの

判断保留・対象外のため意図的に実装していない。

- JTS形式への書き出し、JTSへの反映（Phase 3、保留）
- `JtsBinding` / `baseline_projection` などの差分反映用データ
- 海外locale の検証（データモデルには locale 構造を残している）
- 設定ワークショップ、LLM接続（Phase 4）
- カテゴリテンプレートの編集UI（追加・改名・並べ替え）
- 下書きの自動保存と復旧

### データ破損とその対策（2026-09-15）

GUIでの保存後、久本礼司の `basic_settings` が 4,259字 → 296字 に切り詰められていた。
残っていたのは先頭の1区画【基本属性】だけで、末尾の改行2文字も失われていた。
`python -m ppr restore-origin` で取込原本から復元済み。ほかの7人に影響はない。

**原因は特定できていない。** 現行コードで本文を書き換えるのは `apply_section` の1箇所
だけで、これは区画単位の置換しか行わず、フィールド全体を1区画へ潰すことは構造上できない。
修正前のビルドで保存されたものと考えられるが、イベント列を再現できていないため断定しない。

原因が不明なまま同じ損失を繰り返さないために、次の4つを入れた。

1. **算術的な不変条件** — `set_section` は置換後の長さが
   `元の長さ − 区画の長さ + 新しい値の長さ` と一致することを検査する。合わなければ
   `PersonaIntegrityError` を送出して書き込みを中止し、画面を読み直す。
2. **保存前の確認** — 開いた時点より本文が500字以上かつ30%以上減っていたら、
   増減を示して保存の可否を確認する。
3. **世代バックアップ** — `data/backups/` に最新10世代を残す。直前版1つだけでは、
   破損に気づく前に上書きされる。
4. **編集ログ** — 保存のたびに `data/edit_log.jsonl` へ、本文の増減と直近の変更操作
   （種類・対象・前後の長さ・区画数）を追記する。再発時に原因を追える。

加えて復旧手段を用意した。

- `python -m ppr diff-origin` — ライブラリと取込原本の本文を突き合わせる
- `python -m ppr restore-origin [--persona ID] [--field NAME]` — 取込原本の内容へ戻す

## Phase 2（実装完了 / 作者確認待ち）

### 実装したもの

```
src/ppr/rehearsal/
  capsule.py      人格の描画。予算が足りれば全文、足りなければ原文断片を抽出
  addressing.py   呼称契約。二人称の単複と人類境界
  context.py      build_persona_context（純粋関数）
  contracts.py    RehearsalRequest / SchemeAdapter / RehearsalResult
  runner.py       revision読取、カテゴリ選択、context生成、provider呼出し、記録
  schemes/
    jts_dialogue.py   JTS会話・簡易試演（一括生成）
    sbt_thread.py     SBT架空SNS試演（逐次生成）
src/ppr/llm/
  fake.py         通信しないprovider
  lm_studio.py    LM Studio接続。model keyと推論instance IDを区別する
```

### 人格の渡し方

Phase 0 の実測（§4）に対応する3経路。すべて原文の引用で、LLM要約は使わない。

1. **人格描画** — 予算が足りれば原典を全文（`mode=full`）、足りなければ原文断片を優先度配分で抽出（`mode=capsule`）。既定の予算は24,000字で、8人全員が全文で通る
2. **恒常的な価値境界** — 全文そのまま。適用範囲だけを層3が宣言する
3. **小カテゴリ** — 名前172件だけをモデルへ提示し、選ばれた分の原文を投入。最大4件

`build_persona_context` は純粋関数で、モデルを呼ばない。小カテゴリの選択は runner が行い、結果を引数で渡す。

### モード別扱いの適用範囲

PHASE_PLAN §7.1 の決定を、原文を書き換えずに実現している。原典はそのまま渡し、
「今回適用するのは議論と意見交換だけ。解説・相談は適用しない。保護対象・忌避価値・
反射的反論・同意境界はモードに関わらず維持する」を層3が宣言する。

### 2作品の対比

| | jts-dialogue | sbt-thread |
|---|---|---|
| 生成方式 | 1回のJSON要求で全発言 | 発言ごとに1回ずつ |
| システムプロンプト | 両者を1つにまとめる | 話者ごとに別々 |
| 作品固有の値 | `emotion`（5種） | `post_id` / `reply_to_id` |
| 典拠 | IMPLEMENTATION_PLAN §7.3 | SBT企画書 v3 §7.1・§7.3 |

SBTは企画書 v3 §7.1 の「一括で全会話を書かせず、二体へ別々のシステムプロンプトを
与え、交互に生成する」に従う。計画書 §7.4 の一括生成は使わない。
SBTの仕様は策定中のため、件数・尺・開始話者は `scheme_input` で変更できる。
尺は警告であって失敗条件にしない。

### 検証済みの事項

- 実データ8人の全ペア・両schemeで試演が成立する（Fake provider）
- 同じ人格revisionが両schemeで同一のcontext本文になる
- schemeのソースに8人のIDと名前、ヒロアキが現れない
- 試演でライブラリが変更されない
- 指定revisionと保存内容が食い違えば拒否する
- キャンセルは `cancelled` として `failed` と区別して記録する
- 一覧にないカテゴリ名は推測で寄せずに警告して捨てる
- 小カテゴリの選択では名前だけを送り、本文を送らない

### 未実施

- 実モデルでの試演（`--lm-studio` で実行可能）
- 作者による人物らしさの確認（ACCEPTANCE A項目相当）

### 使い方

```sh
python -m ppr rehearse --lm-studio --scheme jts-dialogue --a <id> --b <id>
python -m ppr rehearse --lm-studio --scheme sbt-thread  --a <id> --b <id>
python -m ppr rehearse --fake ...        # 通信なしで配管だけ確認
```

記録は `data/runs/<run_id>.json` に、送ったメッセージ全文と返った値、
人格contextの要約（描画モード・不採用断片・呼称契約の出どころ・選ばれたカテゴリ）が残る。

## 次の作業

1. 実モデル（LM Studio / Bedrock）で両schemeを実行する
2. 作者が読み、その人物として使えるかを判断する
3. 試演のGUIを作る（現状はCLIのみ）
4. 二重管理の基準日を決める（PHASE_PLAN §7.3）
