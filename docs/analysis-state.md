# シティリーグ環境分析本部 v1.2.2

目的は週次分析の入力と状態を固定することです。分析・予測モデルは追加していません。
`data/city_db.json`、`data/city_decks.json`、`data/index.json`の形式と既存Validateを維持し、収集経路へ台帳更新を接続します。
追加ツールはPython 3.10以上と`requirements-analysis.txt`を使用します。

```sh
python -m pip install -r requirements-analysis.txt
python -m analysis validate-state
python -m analysis validate-cards
python -m unittest discover -s tests -v
```

## 状態ファイルと契約

JSON Schemaは`schemas/analysis-state.schema.json`（Draft 2020-12）です。
`schema_version: "1.2.2"`と`kind`で文書種別を指定します。保存済みv1.2.1文書も引き続き読めます。
`analysis.state.read_state` / `write_state`はschemaと追加の整合性検査を実行します。
書き込みは一時ファイルからのatomic replaceです。同じ状態ファイルへの更新は単一writerで行ってください。
異なるwriterの同時更新を調停する機能はありません。

| ファイル（`data/analysis/`内） | 内容 |
| --- | --- |
| `event_ledger.json` | event_idごとの開催台帳・観測履歴 |
| `deck_classifications.json` | deck_codeごとの分類sidecar |
| `processed_sources.json` | 処理済みページ・動画・ファイルの記録 |
| `research_requests.json` | 調査依頼・状態・結果 |
| `forecast_log.json` | 不変の予測原文・時機・根拠・反証条件・参照snapshot |
| `weekly_snapshots/<week_id>/<analysis_stage>/<snapshot_id>/` | 独自週metadataと入力ファイルの実体（旧ISO保存先も維持） |

### 大会台帳

`event_date`、`category`、`venue_name`、`venue_prefecture`を保持します。
開催状況の`event_status`（unknown / scheduled / held / cancelled / not_held）と、
公開・取得・解析の状態は独立しています。
`observations`の各要素に日時、参照URL、以下を保存します。

| 項目 | 意味 |
| --- | --- |
| `publication_status` | not_published / published / unknown |
| `fetch_status` | not_attempted / success / failed |
| `parse_status` | not_parsed / success / partial / failed |
| `coverage_scope` | unknown / result_list / full_results / top8 / partial_results / collection_feed |
| `published_rank_slots_total` | 公式ページで公開された全順位枠数 |
| `top8_slots_published` | 公式公開されたTop8範囲の枠数 |
| `top8_slots_retrieved` | この観測で正常取得できたTop8範囲の枠数 |
| `top8_decks_known` | この観測で取得したTop8範囲のうちデッキが判明した数 |

分からない数値は`null`、観測していない状態は`unknown`等の明示値です。
0は確認したゼロを意味します。8未満の理由は推測しません。
枠は順位ラベルの種類数ではなく公開された出場者の枠数です（同順位を潰しません）。
取得した一覧が全公開範囲である根拠がなければ公開枠総数を算出しません。
`top8_decks_known <= top8_slots_retrieved`を、両方判明している場合に検証します。

初期台帳は現在の79大会から明示されたID・日付・カテゴリ・プレイヤー所在地だけを転記しています。
過去の`collected_at`から今回の取得成功や公開状態を推測せず、観測履歴は空です。
`legacy_source`は転記元の参照であり、入力版の固定にはスナップショットを使用します。
開催地は`null`で、`players[].player_prefecture`を開催地として使いません。
`events_in_prefecture`も`venue_prefecture`だけを使用します。未判明の開催地は地域別集計に入りません。

観測を追加する例（確認した事実のみを設定してください）：

```python
from analysis.state import append_observation, read_state, write_state

path = "data/analysis/event_ledger.json"
ledger = read_state(path)
ledger = append_observation(ledger, "1115295", {
    "observed_at": "2026-10-03T10:00:00+09:00",
    "source_url": "https://players.pokemon-card.com/event/detail/1115295/result",
    "publication_status": "unknown",
    "fetch_status": "failed",
    "parse_status": "not_parsed",
    "coverage_scope": "unknown",
    "published_rank_slots_total": None,
    "top8_slots_published": None,
    "top8_slots_retrieved": None,
    "top8_decks_known": None,
    "notes": "取得失敗の記録例",
})
write_state(path, ledger)
```

過去の`published`観測は履歴に残ります。今回の取得失敗は中止・未開催・未公開を意味しません。
`write_state`は既存eventの削除や観測履歴の削除・書き換えを拒否します。
追加大会は`records`に明示的に追加して保存できます。
初回転記用の`python -m analysis init-ledger`は既存台帳がある場合に拒否します。
### 収集との接続

Actionsは既存DownloadとValidateの後で`sync-ledger`を実行し、台帳もCommit対象に含めます。
DB転送に失敗しても、失敗履歴を保存するため台帳更新・台帳Commitは実行します。
取得・Validateの失敗自体はjobの失敗として維持し、Validate不合格の3 JSONはCommit対象にしません。
台帳更新が失敗した場合は、DBだけをCommitせずjobを失敗させます。
Actionsでは`requirements-analysis.txt`をインストールします。

```sh
python -m analysis sync-ledger --city .tmp/city_db.json --decks .tmp/city_decks.json --fetch-status success --coverage-scope collection_feed --source-url https://example.com/city-db.json
```

`collection_feed`はTermux結果DBの取得・解析を意味し、全大会の公式結果ページを再取得したという意味ではありません。
観測の`source_sha256`と`source_updated_at`で元DBを識別します。`observed_at`は今回の取得記録時刻です。
順位行がある場合は結果DB由来の公開確認を記録しますが、全公開範囲の根拠がないため公開枠数は`null`です。
Top8取得枠は有効なrank行数、デッキ判明数は60枚デッキ実体がDB内にある枠数です。
同順位をまとめず、8超の同順位行がある場合は推測せずTop8数を`null`にします。

失敗時はその取得元で以前観測した大会と旧city_db転記大会に失敗観測を追記します。
同じ取得元で最後に確認した公開状態を保持し、`published`と最新`fetch_status=failed`が併存できます。
台帳だけに登録した予定大会などは、結果DBから消えている理由を推測しません。
開催地・開催状態は入力に明示された項目だけ反映し、player_prefectureは開催地に転用しません。
`--parse-status failed`は取得したバッチが解析・Validate不合格だったことを記録し、新大会を取り込みません。

`collector/collect.py`も既存index出力後に同じadapterへ接続します。公式一覧のresultリンク観測は
`coverage_scope=result_list`として保存し、取得・処理例外も別状態として記録します。
一覧から順位枠数は算出しません。collectorの既存index生成内容は維持します。

### デッキ分類とカード検査

分類は元デッキJSONから分離しています。`parent_archetype`と複数の`variant_tags`、
`classification_status: classified / partial / unknown`を保存します。
`classified`は親分類必須、`partial`は親分類未確定でも保持可能、
`unknown`は親分類`null`・タグ空配列です。分類時刻・分類器版・根拠も保持できます。
既存分類の割り当てや巨大な辞書の新設は行いません。

60枚検査を維持し、追加検査ではカード枚数の実合計、ID、表示名、種類を確認します。
種類は公式のsectionラベルとの完全一致（末尾の枚数表記のみ許容）で判定します。
「ポケモン」と「ポケモンのどうぐ」を区別し、未知のラベルはWARNです。
カードマスター不在は`MASTER_UNAVAILABLE` WARN、未登録IDもWARNとなります。
マスターがある場合の名前・種類不一致はERRORです。ERRORがあるとCLIは終了コード1を返します。
マスターなしのWARNは整合性を検証済みという意味ではありません。

将来のマスターは以下の形式です。name/aliasesはIDの確かな資料に基づいて設定します。
元データのポケモン名には収録情報が連結されているため、承認した表示文字列をaliasesに登録できます。
推測で文字列を切り取って名前を同一視する処理はありません。

```json
{
  "schema_version": "1.2.1",
  "kind": "card_master",
  "source": "出典をここに記録",
  "updated_at": "2026-10-03T00:00:00+09:00",
  "cards": {
    "example-id": {
      "name": "正式表示名",
      "aliases": [],
      "card_type": "tool"
    }
  }
}
```

`card_type`はpokemon / tool / item / supporter / stadium / energyです。
`python -m analysis validate-cards --master path/to/card_master.json`で使用します。

### 週次入力の固定

```sh
python -m analysis snapshot --week-id W02 --period-start 2026-09-30T00:00:00+09:00 --period-end 2026-10-06T23:59:59+09:00 --analysis-stage INTERIM --cutoff-datetime 2026-10-03T12:00:00+09:00 --retrieved-at 2026-10-03T12:00:00+09:00 --event-id 1115295
python -m analysis verify-snapshot data/analysis/weekly_snapshots/W02/INTERIM/<snapshot_id>

# 旧ISO週CLIも互換性のため維持
python -m analysis snapshot --target-week 2026-W40 --retrieved-at 2026-10-02T23:45:39+09:00 --event-id 1115295
```

独自週の5項目はすべて明示します。`week_id`はW02等の識別子、対象期間は時差付き日時、
`analysis_stage`はINTERIM / FINAL、`cutoff_datetime`は検収時の情報締切です。
期間開始<=終了、締切>=期間開始を検証します。FINALの取得締切が期間終了後になることは許容します。
期間や段階から分析内容や対象イベントを自動推測しません。
`target_week`は旧ISO形式用です。`retrieved_at`は入力を実際に取得した時刻を呼び出し側が指定します。
`created_at`はスナップショットの作成時刻で、取得時刻とは分離します。日時は時差情報必須です。
対象event_id一覧は実際の分析対象を明示し、コピーするcity_dbまたはevent_ledgerに存在することを検証します。
予定・中止・未公開・取得失敗・解析失敗・不明の台帳登録大会も、結果DB未収録のまま対象にできます。
取得済みの全大会がその週の大会とは限らないので、自動で週や対象大会を推測しません。
metadataにはHEADのGit SHA、作業ツリー変更有無、元ファイル参照、コピー先、SHA256を保存します。
デフォルトでは既存3つのJSONと5つの状態ファイルをコピーします。
追加資料・分類器・カードマスターを使う場合は、それらも`--file`で指定してください。
`--file`を指定するとデフォルト一覧を置き換えるため、必要な入力をすべて列挙してください。
`data/city_db.json`は必須です。台帳登録だけの大会を使う場合は`data/analysis/event_ledger.json`も含めます。
コピーしないlive台帳で対象IDを認可することはありません。

同じ週でも一意なsnapshot_idの新しいディレクトリに保存します。
INTERIMとFINALは段階別ディレクトリで分離し、FINAL作成でINTERIMを上書きしません。
既存ディレクトリへの書き込みと`write_state`によるsnapshot metadata更新は拒否します。
元データが後日更新されても`inputs/`のコピーは保持され、再分析はこのコピーを参照します。
作業ツリーに変更がある場合、Git SHAだけでは入力を特定できないため、コピーとSHA256が入力版の根拠です。
metadataは最後に作成します。失敗でmetadataがないディレクトリは未完成として扱ってください。
検証は保存後の改変を検出しますが、外部からのファイル直接編集をOSで禁止するものではありません。

Workで検収された対象週・対象大会・入力版はこのリポジトリに提供されていないため、
初期実装で実分析用のスナップショットは捏造していません。
今後の各分析結果には`week_id`・`analysis_stage`・`snapshot_id`を記録してください。

### 不変のforecast_log

新形式のレコード例：

```json
{
  "forecast_id": "W02-pre-001",
  "created_at": "2026-09-29T23:00:00+09:00",
  "target_week": "W02",
  "forecast_timing": "PRE",
  "forecast_text": "検収で確定した予測原文を保存する",
  "evidence": ["根拠資料の参照"],
  "falsification_condition": "反証される具体的条件",
  "source_snapshot_id": "参照snapshot_id",
  "evaluation_eligible": true
}
```

`forecast_timing`はPRE / LATE / POSTです。正式な精度評価対象を表す`evaluation_eligible`は
PREでtrue、LATE/POSTでfalseをschemaで強制します。予測時機は登録者が事実に基づき指定します。
予測モデルや精度計算は実装していません。
`write_state`は既存forecast_idの原文・時刻・時機・根拠等の変更と削除を拒否します。
訂正・改訂は新しいforecast_idとして追記してください。旧形式forecastも読み取り可能ですが、
PREだったという情報を後から推測補完せず、評価対象フラグを与えません。

## 既存処理との関係と検証

既存workflowのValidate Pythonブロックは基準コミットと同一です。
台帳adapterは既存3 JSONを書き換えません。収集経路への追加は依存インストール・台帳更新・台帳Commitだけです。
unit testはworkflow内の既存Validate Pythonコードを読み取り、一時ディレクトリで
現データの合格・60枚違反の拒否・大会件数減少の拒否を検査します。
ネットワークアクセスやworkflowの手動実行は行いません。
