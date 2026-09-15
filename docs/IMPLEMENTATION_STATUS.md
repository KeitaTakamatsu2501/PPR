# 実装状況

最終更新：2026-09-15

進め方は [PHASE_PLAN.md](PHASE_PLAN.md)、接続契約の実測は [PHASE_0_FINDINGS.md](PHASE_0_FINDINGS.md) を参照。

| Phase | 状態 | 内容 |
|---|---|---|
| Phase 0 | **完了** | 接続契約の実地調査。JTS無変更のまま実台本を1本生成して確認 |
| Phase 1 | **完了** | 人格ビルダー。8人 / ja-JP の取込、編集、保存 |
| Phase 2 | 未着手 | 三層の関節。`build_persona_context` の確定と2作品での実証 |
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

## 次の作業（Phase 2）

1. `build_persona_context` を固定する。Phase 0 の実測3経路に対応させる
   - 人格カプセル（原文断片の抽出、上限つき）
   - 恒常的な価値境界（全文）
   - 小カテゴリのモデル選択（名前一覧を提示 → 選択 → 原文投入）
2. 呼称契約を層3の部品として実装する（`character_addressing` 相当）
3. 2作品で実証する
   - JTS会話（`phase0/probe_b_offline_generation.py` を発展させる）
   - SBTスレッド（新規）
4. 同じ人格revisionが、scheme側のキャラID固有分岐なしで両方に通ることを確認する
