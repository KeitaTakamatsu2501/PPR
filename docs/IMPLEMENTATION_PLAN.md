# PPR v0.1 実装計画

本書の型・パス・コマンドは実装予定の仕様であり、現時点で利用可能なAPIではない。作業順序は `EXECUTION_PLAN.md`、検証項目は `ACCEPTANCE.md` を正とする。

## 1. アーキテクチャと依存方向

PPRを一つのローカルPythonアプリとして実装する。HTTPサーバー、plugin loader、外部DBは不要。

依存方向は `Tk UI → authoring/rehearsal services → domain/storage/provider interfaces`。JTS adapterとSBT試演adapterはこの接点を実装する。domainとstorageはTk、HTTP、JTS、音声へ依存しない。

通常起動でJTSのPythonモジュールをimportしない。コピーしたモジュールが `app_paths`、`dual_voice_chat_core`、`service_database` 等へ連鎖しないよう、必要な型・純粋関数をPPR内へ抽出する。元JTSを使った互換検証だけは隔離subprocessで許可する。

### 1.1 予定ディレクトリ

```text
PPR/
  pyproject.toml
  requirements.lock
  requirements-dev.lock
  start_ppr.command
  config.example.json
  src/ppr/
    __init__.py
    __main__.py
    app.py
    config.py
    domain.py
    storage.py
    revisions.py
    workshops/
      topics.py
      people.py
      service.py
    llm/
      contracts.py
      factory.py
      bedrock.py
      lm_studio.py
      auth.py
      structured.py
      usage.py
      fake.py
    adapters/jts/
      importer.py
      exporter.py
      validation.py
      apply.py
      compatibility.py
    rehearsal/
      contracts.py
      context.py
      runner.py
      schemes/
        jts_dialogue.py
        sbt_thread.py
    ui/
      app.py
      editors.py
      recovery.py
      workshops.py
      jts_exchange.py
      rehearsal.py
    resources/
      topic_templates/jts_194_v1.json
      compatibility/ione_style_v1.json
      schemes/jts_dialogue_v1.md
      schemes/sbt_thread_v1.md
      scenarios/v1.json
  scripts/
    check.py
    check_jts_roundtrip.py
  tests/
    fixtures/
    test_*.py
  data/                       # ローカル、git対象外
    library.json
    drafts/
    imports/
    revisions/
    exports/
    runs/
    usage.sqlite3
    extraction/
  config.local.json           # ローカル、git対象外
```

小さい純粋関数の配置は統合してよい。ただし責務、接続契約、テスト対象を変えない。`data/`の位置は`--data-dir`または`PPR_DATA_DIR`で変更でき、未指定時はPPR直下を使う。

## 2. 抽出元と移し方

### 2.1 直接の出発点

- `high_quality_character_manager_gui.py`：Tkのリスト、基本編集、topic/person editor、workshop、復旧。GUIを一度に全面再設計せず、依存をサービスに置き換える。
- `high_quality_topic_workshop.py`：質問プロンプト、カテゴリ参照、清書スキーマ、解析を移す。
- `high_quality_person_workshop.py`：対象ID・順序・確認済み判定・清書・レビュー後適用を一組で移す。
- `high_quality_character_profiles.py`：194カテゴリ、本文の構成、人物関係の扱いを参照。既存loaderをraw移行に使用しない。
- `character_profiles.py`：固定v8のフィールド・ID・JTS書き出し条件を参照。音声必須の型をPPR人格型に流用しない。
- `character_locale_store.py`：4locale、配置、locale分離を継承。単一localeの二ファイル順次保存をPPR本体の保存方式にはしない。
- `character_style_overlays.py`：JSON外の補助を一覧化し、必要なデータと純粋な選択処理を抽出。

### 2.2 必要部分だけ抽出する依存

- `dual_voice_chat_core.py`：`LMModel`相当、設定・通信例外、LM Studio通信・JSON解析。音声探索、TTS、台本機能は通常起動に持ち込まない。
- `language_model_provider.py`：localのprovider設定解決とfactory。`ServiceExecutionTarget`、productionサービス制約、Fact ensembleを除く。
- `bedrock_mantle_client.py`：`BedrockMantleClient`が使うHTTP、認証、schema変換、JSON解析、エラー・usage情報。必要helperの依存を追って移す。Luna/Qwen/ConverseのFact用経路は今回不要。
- `desktop_language_model.py`：動作の参照だけ。`ServiceDatabase`、admission、Web課金台帳を丸ごとコピーしない。
- `character_value_contracts.py`：価値境界と作品別表出の設計資料として参照。既存の全契約・意味監査を試演の必須経路にしない。
- `offline_generation.py`：現行JTSの比較元。巨大な生成器をPPRにコピーしない。

### 2.3 コピーでは解決しない箇所

JTSは新規作成、保存、データ型の三箇所で音声を必須にしている。GUIから音声欄を消すだけで完了にしない。起動時の音声探索、音声由来のpersona ID生成、音声IDの一対一制約も分離する。

またJTSの名前付きキャラ補助を「汎用構成作家」の中に残さない。イオネの語彙補助は人格に付随する資料として扱い、ヒロアキの完全対立などJTSの配役規則はJTS側演出として記録する。

## 3. ネイティブの人格・保存形式

### 3.1 libraryと人格を分ける

`library.json`は次を持つ単一JSON object。書き込みは同ディレクトリの一時ファイル→flush/fsync→`os.replace`とし、破損時の直前版を保持する。GUIは一プロセスで編集し、library用のプロセスロックを取得する。二つ目のプロセスは閲覧または明示エラーにする。

- `schema_version: 1`
- `personas: list[PersonaRecord]`
- `topic_templates: list[TopicTemplate]`
- `project_bindings: list[JtsBinding]`
- `imports: list[ImportReference]`

取込原本はlibraryに埋め込まず`data/imports/`へ保存する。元JSONへの対応位置・未編集判定に必要なprojectionは`JtsBinding`で保持する。認証情報、試演出力、usageはpersonaに入れない。

### 3.2 PersonaRecord / PersonaDocument

`PersonaRecord`は`persona_id`、`archived`、`variants`を持つ。`variants`はlocale文字列→`PersonaDocument`の対応。既存IDはそのまま使い、新規は`ppr-`＋UUIDを一度だけ発行する。名前はIDに使用しない。locale違いでIDを再発行しない。

`PersonaDocument`の必須キー：

- `persona_id`, `locale`, `name`
- `first_person`, `second_person`
- `species_label`, `humanity_membership`
- `overview`, `basic_settings`, `speaking_style`, `dialogue_samples`
- `topic_template_id`
- `topic_stances`：順序付き配列。各要素は`entry_id`, `category`, `stance`。
- `person_stances`：順序付き配列。各要素は`entry_id`, `name`, `stance`, `target_character_id`, `direct_address_override`, `perceived_humanity_override`。
- `supplements`：後述の人格補助資料。
- `extensions`：PPR形式の将来拡張を保持するobject。

文字列は保存時に一律strip、Unicode正規化、改行の置換をしない。入力検証で空白だけかを確認しても、保存値は作者が入力した文字列のままとする。Tk Textの取得は`end-1c`を用い、Tkが付ける末尾改行だけを除く。内容末尾の空白・改行を`.strip()`で消さない。

新規の未完成人格も保存できる。試演時は名前とoverview/basic_settingsの少なくとも一方の実質的本文を要求する。JTS書き出し時の必須条件は別に検証する。

`entry_id`はPPR内部で行を識別するために付ける。既存配列に同名カテゴリがあっても行を勝手に統合しない。旧関係が名前だけでも名前からIDを推測しない。未知相手ID・削除済み相手ID・本文が空で呼称例外だけの関係も保持する。

`humanity_membership`は既存値を引き継ぐ。PPRの人物を人類中心の分類に強制しない。未使用なら空欄で保存でき、JTS向けの必要値は書き出し時に補う。既存の値を勝手に空欄へ変えない。

### 3.3 カテゴリと人物関係の編集

- 初期テンプレートは既存194カテゴリの順序と文字列を保持する。
- テンプレートを複製してカテゴリの追加・改名・並べ替えができる。`/`を階層表示の区切りにする。
- 新規テンプレート内の同名categoryは拒否する。取込元に既に重複がある場合は行を保持して識別子を表示し、該当カテゴリのワークショップは作者が重複を整理するまで対象外とする。自動mergeで原文を消さない。
- 改名時は対象人格の対応行も同じ操作内で更新し、他の人格や元テンプレートは変更しない。
- 本文付きカテゴリの削除は本文を表示した明示操作。テンプレート変更だけで本文を削除しない。
- 「恒常的な価値境界」は原文を保持し、試演contextの常時参照対象にする。
- 人物関係ワークショップの名簿はPPR内で選んだ対象。JTSのenabledや音声有無で自動排除しない。選択名簿を保存し、途中の対象変更で古い生成結果を別の人物へ適用しない。
- PPRでは同名別IDを保持できるよう短いIDも表示する。JTSの同名禁止は出力bundleでだけ確認する。

### 3.4 revisionと作者メモ

保存済み`PersonaDocument`をcanonical JSON（UTF-8、key順固定、配列順保持、NaN禁止）にし、SHA-256から`revision_id`を作る。同じ内容は同じrevisionを再利用する。保存先は`data/revisions/<revision_id>.json`。

revisionの対象に音声binding、scheme、model、生成日時、作者メモを入れない。試演中はrevisionの値だけを読み、編集中のGUI stateを読まない。未保存変更がある状態で試演する操作は、まずPPRに保存してrevisionを作ることを画面に示す。

作者メモはrun単位に`unreviewed / usable / needs_adjustment`と自由記述を保存する。これは人物の正しさを判定する自動スコアではない。別revisionや別roleへ評価を自動継承しない。

### 3.5 人格補助資料

`supplements`は`id`, `kind`, `content`, `data`, `source_ref`, `editable`を持つ。v0.1で実装するkindは`style_text`と`topic_lexicon`だけ。Pythonコードや任意の式を実行する拡張DSLは作らない。

取込時にイオネの既存語彙・スタイルを抽出し、source hashとともに互換資料として登録する。既存のpolicy refによる重複抑止と話題別選択の純粋処理を保つ。実行時はpersonaに付いたsupplementを処理し、schemeが特定のキャラIDを見て分岐しない。

元JTSにコードとして残る補助はv0.1では読み取り専用の互換資料として表示する。JTSへ戻す際は元本文へ二重に埋め込まない。新しくPPRで追加する一般的な補助は編集可能だが、JTS出力で表現できない補助がある場合は対応外項目として表示し、無言で捨てない。SBT試演では人格に付随する補助を利用できる。

JTSがloaderで適用する旧イオネ本文の補正等は別の互換レポートへ記録する。原文を自動修正して人格保存版を発行しない。

## 4. JTS import/export契約

### 4.1 入力と検出

JTS rootを指定し、以下だけを正規入力として検出する。

```text
config/high_quality_generation/fixed_characters.json
config/high_quality_generation/characters.json
config/high_quality_generation/locales/en-US/{fixed_characters,characters}.json
config/high_quality_generation/locales/ko-KR/{fixed_characters,characters}.json
config/high_quality_generation/locales/zh-TW/{fixed_characters,characters}.json
```

上記brace表記は二つの実ファイルを意味する。存在しないlocaleは作らず、バックアップや`.bak`を自動で最新扱いしない。追加localeはPPR側で作成できるが、v0.1のJTS出力対応はこの4localeに限定する。

一方しか存在しないペア、重複ID、不正JSON、固定側にない詳細IDは診断を出し、正式なlibraryへ部分取込しない。未知schema versionは原本保管と診断までとし、編集可能な対応版へ勝手に変換しない。JSON objectの重複keyも検出し、自動的な後勝ち処理をしない。

原本は`data/imports/<import_id>/files/<JTSからの相対パス>`へbytesのままコピー。manifestに相対パス、hash、schema、読取時刻、元JTS root、コード参照hashを記録。取込の前後で元ファイルのhashを比較し、読取途中の変更を検出したら取込を再開せず診断する。

### 4.2 JtsBindingと保持フィールド

`JtsBinding`は`persona_id`, `locale`, `import_id`, `source_character_id`, `source_document_refs`, `baseline_projection`, `fixed_overrides`, `export_enabled`を持つ。原JSONツリーはimport原本から再構成し、未知フィールドと未編集値を保持する。

固定v8で保持する項目：

```text
id, name, enabled, first_person, second_person, species_label,
humanity_membership, voice_profile_id, reference_audio, reference_text,
voice_volume_percent, reference_sets,
default_personality_id, personalities,
default_one_to_one_personality_id, one_to_one_personalities
```

`personalities[]`の`id/name/description/overview/speaking_style/dialogue_samples/behavior_guidelines`、`one_to_one_personalities[]`の`id/name/prompt`を全て保持する。詳細プロフィールの本文と固定側templatesを重複として統合しない。

詳細v2は`character_id/overview/basic_settings/speaking_style/dialogue_samples/topic_stances/person_stances`と未知フィールドを保持する。

音声の相対パスはGPT-SoVITS root基準であり、PPRやimportファイル基準に解決しない。元のパス文字列を保持し、必要時だけbindingに保存した基準rootで解決する。PPRの取込・保存に音声ファイルの実在を要求しない。

### 4.3 編集差分の反映

- PPRのname/一人称/二人称/種族等の変更は対応する固定行だけへ反映。
- 詳細4本文、topic/person配列は対応する詳細行だけへ反映。
- 固定templates、音声、enabledは「JTS接続情報」で明示編集されたときだけ変更。
- JTS形式にない`entry_id`等は書き出さない。
- 無編集の文書は元bytesを出力。編集した文書はJSONの書式が変わってよいが、指定箇所以外の値、文字列、未知フィールド、配列順を維持する。
- 既存行の未知フィールドを保つため、配列全体を単純な既存serializerで再作成しない。行IDに対応する原行へ差分を適用する。
- libraryでarchiveしただけではJTSから削除しない。v0.1の反映は追加・更新・enabled切替を扱い、既存人格の物理削除は行わない。
- 一人の更新でも元の全名簿を含むペアを作る。29人の名簿を一人で置き換える事故を防ぐ。件数29自体は固定しない。

同じpersona ID/localeを再importしたときは、baselineからPPRも元JTSも変わった項目を競合として表示する。自動的な後勝ちやLLMによるmergeをしない。競合していない読み取りや別人格の編集は利用できる。

### 4.4 新規PPR人格のJTS出力

音声なしの新規人格はPPRで作成・保存・試演できる。JTSへ追加するときだけ、JTS接続情報を設定する。

- `voice_profile_id`、通常参照音声、通常参照テキストを必須とする。架空のパス・音声IDで通さない。
- 参照ファイルはfile pickerで選択可能。モデルの学習やTTS起動はしない。元JTSから既存bindingを参照する機能は用意してよいが、名簿内の音声重複を検証する。
- JTS接続画面の「音声接続を確認」でだけ、対象JTSのmetadata discoveryを隔離subprocessから実行し、選択したvoice IDと必要な資産を確認する。`dual_voice_chat_core.py`の`discover_voice_profiles`を参照し、TTS/音声モデルを起動しない。通常起動・人格保存にはこの処理を入れない。JTS loaderが読めることだけでは実在確認にならない。書き出しは「形式上互換・音声接続未確認」として保存できるが、新規/変更bindingの反映には対象環境での確認を要求する。既存未変更bindingを未確認の新しい音声へ置き換えない。
- 固定templatesの初期案は人格本文から機械的に対応づけて表示する。既存JTS人格のtemplatesは変更しない。
- 新規のgroup templateはID `ppr-default`、名前 `PPR`、description=basic_settings、overview/speaking_style/dialogue_samplesは同名本文、behavior_guidelines=basic_settingsを初期案にする。新規one-to-one templateはID `ppr-default-one-to-one`、名前 `PPR`、promptは4本文を見出し付きで連結した初期案にする。作者が書き出し画面で確認・編集できる。設定を推測するLLM呼び出しはしない。
- `default_*_id`は作成したtemplate IDを参照する。JTSが要求する名前・代名詞・本文が空なら具体的な不足項目を表示する。
- `reference_sets.normal`には指定した通常参照を使う。追加の感情別参照は任意。新規bindingの音量は100、範囲0〜100。空のhumanity_membershipはJTS出力時のみ`unspecified`、未指定のperceived_humanity_overrideは`inherit`へ対応づける。元に値があれば変更しない。
- JTS側名簿全体でID、casefoldした名前、voice_profile_idの一意性を検証する。PPR全体へこの制約を持ち込まない。

### 4.5 exportの公開

`data/exports/<export_id>/bundle/`に、`fixed_characters.json`と`characters.json`、必要な`locales/`をまとめる。書き出しの単位は接続先library全体または選択localeの全名簿。変更対象だけを差分manifestにも記録する。

一時ディレクトリへ全てを書き、schema・参照・JTS loader互換を検証してから、新しいexportディレクトリとして公開する。途中失敗では完成扱いのexportを作らない。原JTSへの書き込みはこの操作に含めない。

## 5. JTSへの明示反映と復旧

UIの「JTS形式で書き出す」と「JTSへ反映」を別の操作にする。通常保存、自動保存、起動、試演に反映の副作用を付けない。

反映画面は対象JTS root、locale、全名簿件数、追加・変更する人物、選択した人格revision、変更するファイルを表示する。既存の取込時hashと現在の対象hashを比較する。異なれば再取込・差分確認を要求し、強制上書きを既定にしない。

**現行JTSは二ファイルを一括snapshotとして読まないため、外からの二ファイル置換を完全に原子的にはできない。** v0.1は、対象JTSのアプリ/サービスとキャラクター編集画面を停止した状態での明示反映に限定する。画面にこの前提を示し、操作時に確認する。PPRから他プロセスを勝手に停止しない。

一回の反映は一localeのペアを単位とする。

1. exportが検証済みであることと、対象hashを再確認。
2. 元ペアをPPR側のexportバックアップへbytesで保存し、hashを記録。
3. 対象と同じディレクトリへ二つの一時ファイルを書き、flush/fsync。
4. 更新journalを永続化し、`pending`を記録。
5. 固定・詳細を順に`os.replace`し、各段階をjournalへ記録。
6. 読み戻しhashが検証済みexportと一致したら`target_committed`。
7. 途中失敗はバックアップから復旧する。復旧も失敗したら`recovery_required`と実際のhashを記録し、対象に対する次の反映を止める。

8. `target_committed`後、反映したペアのbytesを新しい不変import snapshotとして追加し、対象bindingのsource参照、baseline_projection、次回比較用hashを反映した版へ進める。現在のPersonaDocumentは変更せず、反映版より新しい編集中の内容も保持する。原import snapshotは消さない。
9. binding更新まで保存できたら`committed`。対象更新後にPPR側の保存だけ失敗した場合は`binding_refresh_pending`とし、再開時に対象hashが反映済みexportと一致することを確認してbinding更新だけを再実行する。二つの対象ファイルをもう一度書き換えない。

これにより、自分が前回反映した変更を、次回の外部競合として誤検出しない。失敗復旧で対象を旧版へ戻した場合、baselineも実際に復旧した版へ合わせ、作者の現在の編集内容は保持する。

起動時に未完journalがあれば復旧画面を示す。第三者が追加編集していた場合は上書き復旧せず、元/新/現在のhashとバックアップ場所を表示する。復旧ボタンは対象の停止状態を前提にする。

開発中の自動検証・実装AIの操作は複製したJTS配置だけへ反映する。本番JTSへは作者が完成アプリの操作で反映する。

## 6. LLMとワークショップ

### 6.1 小さいprovider interface

`LanguageModelClient`は少なくとも次を提供する。

```python
list_models() -> list[ModelInfo]
chat_json(model_id, messages, temperature, max_tokens, *,
          schema_name, schema, request_context) -> StructuredResult
```

`ModelInfo`は`model_key`、`inference_id`、表示名、context上限（不明ならnull）、providerを持つ。LM Studioの読込済みinstance IDとmodel keyを同一視しない。GUI選択はModelInfoを保持し、`chat_json`の実際のmodel_id引数には`inference_id`を渡す。ログには両方を記録する。LM Studioの既存reasoning設定とmodel keyの逆引き処理もadapter内で継承する。Bedrockでは両IDが同じでもよい。

`StructuredResult`は解析したvalue、raw応答、finish reason、provider request ID、usage、曖昧さ/打ち切り等のdiagnosticsを持つ。providerでJSONとして読めたことと、アプリの契約を満たしたことを区別する。

UIに通信型を直接漏らさず、workshop serviceがvalueを既存parserに渡す。providerが未設定でもアプリは起動し、接続・model一覧更新は明示操作または最初のLLM操作時に行う。

### 6.2 設定の継承

初回に指定したJTSの設定から、`language_model_provider`, `bedrock_profile`, `bedrock_region`, `bedrock_model_id`, `bedrock_service_tier`, `bedrock_project_id`, `lm_studio_url`だけを読み取り、PPR設定として保存できる。環境変数のJTS相当項目も初回取込の候補にする。

その後の優先順位は`PPR_*`環境変数→PPR設定→抽出したJTSコードのlocal既定値。JTSの設定ファイルを通常起動の必須依存にしない。`PPR_CONFIG`で設定ファイルを選べる。AWS認証は既存profileと標準資格情報チェーンを参照し、credential/tokenそのものをコピーしない。

providerを変更するのは作者の明示選択のみ。GemmaのモデルIDやFlex等は抽出時の設定・定数を継承し、勝手に別モデルや高いtierへ切り替えない。

### 6.3 transport・usage

JTSから抽出する挙動は、schema sanitize、structured JSON fallback、401時の資格情報更新、429処理、HTTP error、read timeout、空応答、打ち切り、usage欠落の識別。既存テストから該当ケースを移す。

`usage.sqlite3`はPPR専用。最小限、attempt ID、logical_call_id、physical_attempt_index、stage_id、run/workshop ID、provider/model_key/inference_id/tier、開始/終了時刻、status、token usage（不明はnull）、provider request ID、error codeを記録する。送信前に開始を永続化できなければ送信しない。成功・失敗・曖昧な結果を全て終端記録に残す。token不明を0や成功と偽らない。価格を計算する場合はJTS由来の価格参照版も記録し、初期版の必須機能にはしない。

応答取得後に終端記録だけ失敗した場合は「応答取得済み・記録未完」とし、raw応答をローカル復旧ファイルへ保存する。ディスク書き込み自体が不能ならメモリ上の応答を保持してエラーを表示する。モデルへ自動再送せず、同じattempt IDの終端記録だけを再試行する。記録が完了するまで次stage・人格への適用・成功表示へ進まない。終了して応答を失った場合も未完記録を成功に変えない。

HTTP送信の自動再試行は、明示的に再試行可能な認証/制限応答と、既知のschema非対応を示すHTTP 400に対する一回のstructured-output fallbackに限定する。fallbackも含め、一論理呼び出しにつき初回を含め最大3送信とする。単なる400を全てfallback対象にしない。

送信後のtimeout/5xx等、処理済みか不明な結果はPPRでは自動再送しない。これはJTSの一部read-timeout再送からの意図的な差分である。PPRにJTS全体の台本checkpoint/回復処理を移さないため、曖昧な結果を記録して作者の明示的な再実行へ戻す。該当する既存テストはこの仕様へ読み替える。接続先・認証・schema処理の継承と、JTS全体の再開制御の継承を混同しない。

応答内容の形式不正は、応答を受領済みで課金上の曖昧さがない場合だけ、adapterが一回まで修復要求できる。人格が気に入らないことを理由に自動再生成しない。課金や実行済み状態が曖昧な失敗では、結果を残して明示的な再実行へ戻す。

worker thread、queue、Tk `after`でUIへ結果を返す。cancelは次のモデル呼び出し・修復・適用を止める。送信済みHTTPの取消を保証したと表示せず、結果が到着しても人格には適用しない。アプリ再起動時の未完attemptは履歴として残し、モデルへ自動再送しない。

### 6.4 ワークショップの保持仕様

- トピック：大カテゴリ→作者との質問→理解要約→全対象小カテゴリの清書→編集確認→選択項目へ反映。
- 人物：対象名簿とIDを固定し、相互関係を推測せず、回答者から見た感情を聞く。未確認の対象が残っているときはモデルのreadyをそのまま信じない。
- parserは既存の短い対象index/旧ID形式の対応と検査を継承する。unknown/missing/duplicate対象を勝手に推測して埋めない。
- 生成結果は作者の反映操作まで下書き。別persona/locale/対象名簿へ切り替わったら古い結果を誤適用しない。
- 自動保存は2秒間隔で変更がある場合のみ。persona/locale、dirty本文、質問履歴、理解要約、ready、未送信入力、参照カテゴリ本文、生成後未反映の清書、対象IDと順序を保存する。
- 保存失敗は画面に示し、未保存の状態を維持する。終了時の復旧保存失敗を成功として閉じない。

## 7. 試演と作品の関節

### 7.1 共通要求・結果

`RehearsalRequest`：

- `request_id`
- `scheme_id`, `scheme_version`, `role_text`, `role_digest`
- `cast[]`: `{actor_ref, persona_id, locale, revision_id}`。actor_refは今回の配役、persona IDは人物自身。
- `scenario`: 話題、場面、素材の種別（third_party_claim/observation/quoted_criticism）、素材の発言元。架空素材を初期値とする。素材の引用は自動的にA/Bの信条にしない。
- `context_options`: 参照するtopic、関係、補助の選択方法。
- `model_settings`: provider/model_key/inference_id/temperature/max_tokens/context上限。
- `scheme_input`: 作品固有の値。共通domainへ返信IDやemotionを強制しない。

`RehearsalResult`：

- `run_id`, `request_id`, `status`（success/failed/cancelled）
- 共通表示用`utterances[]`: `{utterance_id, actor_ref, text}`
- `scheme_output`：作品adapter固有の結果。
- 人格revision、role/scheme版、context選択、model設定、stageごとのrequest/response/usage、開始終了時刻、warnings/errors。
- 作者メモと比較対象run ID。モデルに与えるpersona本文には入れない。

`SchemeAdapter`の責務は`validate_input`、stageごとのprompt/messages/schema構築、応答parse、作品別検証、共通表示への変換。runnerはrevision読取、共有context生成、provider呼出し、cancel、ログ保存だけを担当する。

registryは当面`jts-dialogue@1`と`sbt-thread@1`の二つをコードで登録する。将来の外部pluginロードは作らない。role本文はresourceの初期版をUIで編集でき、変更時はrunに本文とhashを記録する。作者のrole編集はpersona本文を変更しない。

### 7.2 人格contextの作り方

両schemeは同じ`build_persona_context`を使用する。初期値はidentity、4本文、恒常的価値境界、全topic stance、出演する相手への関係、対応supplement。既知の相手IDを先に使い、名前のみの旧関係は未確定として保持する。関係を全出演者へ共有する設定資料として構成作家に渡し、実際の台詞で相手の未公開内面を既知にする指示はしない。

原文の「恒常的な価値境界」にも`【モード別扱い】`としてJTSの議論/意見交換/解説/相談向け指示が含まれる。原文は保存したまま、context内では人格原典として区切って渡し、現在のscheme・出力契約と区別する。JTS簡易試演は意見交換モードを選び、その条件付き指示を適用する。SBTはJTS四モードを選択せず、そこに書かれた進行・文章量の指定を無条件適用しない。保護価値・忌避・同意境界は維持する。roleは出力形式や場面を定めるが、人格の価値判断を上書きしない、という適用規則を両promptに明記する。v0.1で原文をLLMに再分類・改稿させる必要はない。

全量がcontextに収まらない場合、黙って切り捨てたり要約したりしない。UIで「選択トピック」へ切り替え、作者が選んだcategoryの原文と核を使う。roleを変えても選択を勝手に変えない。context本文・選択・hashをrunへ記録する。

利用可能ならproviderのcontext上限を使う。厳密なtoken数が分からない見積もりはその旨を表示する。providerが長さ超過を返したら、入力を保ってエラーを示す。見積もり値を根拠に人格本文を自動削除しない。

### 7.3 JTS会話・簡易試演

初期値：二人、6発言、話者はA/B交互。上限は12発言。構成作家への1回のJSON要求で生成する。temperatureは同一比較で固定でき、max_tokensは初期4096、UIで変更可能。

adapter内の応答形式例：

```json
{
  "lines": [
    {"speaker": "a", "text": "短い発言。", "emotion": "normal"},
    {"speaker": "b", "text": "相手への返答。", "emotion": "serious"}
  ]
}
```

許可emotionはJTSのnormal/angry/happy/sad/seriousを継承。件数、話者、順序、空本文を検証する。台本形式はJTS adapterの内部契約であり、personaに入れない。

UI名は必ず「JTS会話・簡易試演」。現行Adaptive生成、外部の事実確認、音声生成、完全対立等を再現したと表示しない。

分離前後の実JTS比較は、既存JTSログと、PPR書き出しを実JTSで使用したログを作者が取り込んで並べられるようにする。v0.1のログ取込はUTF-8テキスト貼付/ファイル読取と任意のmodel・話題メタデータでよい。出典不明の値は不明と表示し、PPR簡易試演を旧JTS出力として扱わない。

### 7.4 SBT架空SNS試演

二人、一本の返信鎖。初期値は根投稿1件＋返信5件、合計6件。合計の選択範囲は4〜7件。返信はBから開始してA/B交互。

stage 1：Aの根投稿を生成。応答は`{"text":"..."}`。max_tokens初期1024。

stage 2：確定した根投稿と二人の人格を渡し、残りの返信を一括生成。max_tokens初期4096。

```json
{
  "replies": [
    {"actor_ref": "b", "text": "その投稿への反応。", "reply_to_index": 0},
    {"actor_ref": "a", "text": "反応への返答。", "reply_to_index": 1}
  ]
}
```

上記は形の例であり実要求では指定件数を要求する。根投稿のindexは0、返信iの返信先は直前indexに固定。永続post IDはPPRが採番する。未知話者、未来参照、件数不一致、空本文を拒否する。共通表示に加え、`scheme_output.posts[]`にはpost ID・reply_to ID・actor・本文を保存する。

構成作家は短い投稿、相手の具体的主張への反応、応酬の区切りを指示する。全員への罵倒義務、承認欲求の追加、勝利のための信条撤回、クラス価値の上書きはしない。未設定の年収・職業・共有経験を捏造しない。SBT既存system.mdは役割だけではなく価値観を含むので丸ごと流用しない。

最後は応酬の区切りとし、ゲームの勝敗・謝罪強制は実装しない。中立的な話題で同意することや対立が深まらないことも、形式が正しければ有効な試演として表示する。

### 7.5 試演UIと保存

二人のpersona/locale/revision、scheme、role本文、架空素材、モデル設定、context選択、実行/キャンセル、結果、比較対象、作者メモを持つ。結果は左右または上下に並べて読める。比較条件の違いを表示し、自動優劣スコアは出さない。

run開始時に入力を保存し、各stage終了ごとに追記する。stage 2失敗時も成功した根投稿を保持し、次回の明示再開では元runの人格revision・role本文・context・model設定とその根投稿を再利用する。現在のGUI状態を読み直さない。条件を変更する場合は新runを作る。試演結果を人格の記憶や設定へ自動追記しない。

## 8. 初期試演素材

人物名や正解を素材に埋め込まない。二つのroleで同じscenarioを選べるよう、共通resourceに次の三つを用意する。

素材の引用は「架空の第三者の主張/観察」として提示し、出演者Aが同じことを主張したと決めつけない。SBTの根投稿はAが素材をどう捉えるかから生成する。v0.1の組み込みシナリオは既存のA/B発言を強制する継続会話を扱わない。外部JTSログ取込は比較・閲覧用であり、この素材へ無断転用しない。

1. 架空の町で、湿地を埋めて道路を延ばすと通勤が15分短くなる。「反対する人は町の成長を邪魔している」という主張。
2. 「雨上がりの庭を歩いた。花より濡れた葉を見るのが好き」という穏やかな話題。
3. 「その主張は、自分の気持ちを全員のルールにしたいだけでは」という相手の批判。

人物らしさは作者が読む。fixtureテストに「テルミナスは必ず反対」等の意味的正解を埋め込まない。

## 9. 開発・起動・操作コマンド

Python 3.14 / Tk 9を前提とし、PPRのpyprojectもそれに合わせる。JTSの `<3.13` 制約やGPT-SoVITS Pythonへのfallbackをコピーしない。`start_ppr.command`はPPR専用venvを使い、見つからなければセットアップ手順を表示する。

最低限の依存はrequests、aws-bedrock-token-generator、botocore[crt]。元JTS lockの動作確認済み版を起点に専用lockを作る。画像編集/TTSを実装しない限り、そのためだけの依存を追加しない。テストはunittest、lintは既存JTSと同系統のruff設定を使う。

実装後に提供するコマンド：

```sh
/opt/homebrew/bin/python3.14 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.lock
.venv/bin/python -m pip install -e . --no-deps
.venv/bin/python -m ppr
.venv/bin/python -m ppr --offline --data-dir /tmp/ppr-smoke
.venv/bin/python scripts/check.py
.venv/bin/python scripts/check_jts_roundtrip.py --jts-root /Users/keita/dev/private/JTS
```

`--offline`はネットワークと資格情報解決を呼ばない。編集と明確にfixture表示されたFake provider試演を利用できる。Fake出力を実モデルの結果として記録しない。

`check.py`はlint、compile、unittestを実行し、いずれか失敗なら非zero終了する。`check_jts_roundtrip.py`はtempdirへコピー/書き出しし、原JTSに書き込まずhash・値・loader互換を検証する。実LLMリクエストと本番反映を標準checkへ含めない。

## 10. 実装時に作る運用文書

`IMPLEMENTATION_STATUS.md`にE0〜E9の状態を、`VALIDATION_REPORT.md`に検証結果を記録する。READMEには初回取込、接続設定、編集、試演、書き出し、停止状態でのJTS反映、復旧、対応schema/localeを記載する。

初期版の人物らしさについて書ける事実は「同じ設定版を使った」「作者がこのrunを利用可能とした」まで。機械検査だけを根拠に「人格同一性を確認済み」と書かない。
