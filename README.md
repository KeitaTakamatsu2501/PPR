# PPR

作品から独立した人格を作成・編集し、複数のプロジェクトで使い回すためのデスクトップアプリ。

一度作った人格をその作品の中だけで終わらせず、資産として育てることを目的にする。

## 状態

Phase 1（人格ビルダー）を実装中。JTSの8人をPPR形式へ取り込み、JTSなしで編集できる。
JTSへの書き戻しは判断保留（[docs/PHASE_PLAN.md](docs/PHASE_PLAN.md) §3）。

## セットアップ

```sh
/opt/homebrew/bin/python3.14 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
```

## 使い方

JTSから人格を取り込む（読み取りのみ。JTSへは一切書き込まない）。

```sh
.venv/bin/python -m ppr import-jts --jts-root /Users/keita/dev/private/JTS
```

編集画面を開く。

```sh
./start_ppr.command
# または
.venv/bin/python -m ppr
```

そのほかのコマンド。

```sh
.venv/bin/python -m ppr list          # 取り込み済みの人格を一覧
.venv/bin/python -m ppr check-drift   # 取込後にJTS側が変化したかを調べる
.venv/bin/python scripts/check.py     # lint / compile / テスト
```

`--data-dir` または環境変数 `PPR_DATA_DIR` でデータ置き場を変更できる。
`--offline` はネットワークと資格情報解決を一切呼ばない。

## 編集画面

- **基本** — 名前、一人称、二人称、種族・存在種別、人類との関係。音声は扱わない。
- **本文** — 概要 / 基本設定 / 口調・話し方 / セリフのサンプル。本文は `【...】` 見出しで
  区画に分かれており（実データではミラが62区画）、区画単位で編集する。ほかの区画は
  1文字も変わらない。
- **価値観** — 195件のトピックスタンス。大分類ごとにまとめ、「恒常的な価値境界」を先頭に
  固定する。
- **人物関係** — 相手のID、呼称の例外、人類認識の例外、関係の本文。PPR内に存在しない
  相手を指す関係も保持し、その旨を表示する。

名前を変えても `persona_id` は変わらない。localeを編集しても他のlocaleは変わらない。

## データ

| 場所 | 内容 |
|---|---|
| `data/library.json` | 人格の正規保存。一時ファイル → fsync → `os.replace` で原子的に更新し、直前版を `library.prev.json` に残す |
| `data/imports/<id>/` | 取込原本のbytesとSHA-256 |
| `data/revisions/` | 保存済み人格の版（canonical JSONのSHA-256） |
| `data/library.lock` | 編集中のプロセスロック。二重起動を防ぐ |

`data/` は追跡対象外。人格本文はリポジトリに入らない。

## 設計の約束

- 文字列は保存時に strip / 正規化 / 改行変換をしない。作者が入力した文字列のまま保つ。
- 配列は順序を保つ。同名カテゴリがあっても行を勝手に統合しない。
- 未知の相手ID、削除済みの相手ID、本文が空で呼称例外だけの関係も保持する。
- 通常起動でJTS/MyFriends/音声探索/ネットワークを呼ばない（`tests/test_isolation.py` で検証）。
- 元JTSへは書き込まない。取込の前後でハッシュを比較し、読取中に変化していたら中断する。
- 人格の同一性を数値で証明することは目標にしない。`revision_id` は「同じ設定を使用した証拠」に限る。

## 文書

1. [フェーズ計画](docs/PHASE_PLAN.md) — 進め方の正。目的、三層、確定方針、Phase 0〜4。
2. [Phase 0 実測結果](docs/PHASE_0_FINDINGS.md) — 人格と構成作家の接続契約の実測。
3. [実装計画](docs/IMPLEMENTATION_PLAN.md)、[実行計画](docs/EXECUTION_PLAN.md)、
   [受入・検証計画](docs/ACCEPTANCE.md)、[引き継ぎ指示](IMPLEMENTATION_HANDOFF.md) — 経緯資料。
   フェーズ計画と食い違う場合はフェーズ計画を優先する。
4. [調査時の参照元](docs/SOURCE_BASELINE.json) — 参照したJTS/SBTのファイルとハッシュ。
