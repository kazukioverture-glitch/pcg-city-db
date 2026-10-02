# シティリーグ環境分析本部 v1.2.1

目的は週次分析の入力と状態を固定することです。分析・予測モデルは追加していません。
`data/city_db.json`、`data/city_decks.json`、`data/index.json`、収集コード、既存workflowはそのままです。
追加ツールはPython 3.10以上と`requirements-analysis.txt`を使用します。

```sh
python -m pip install -r requirements-analysis.txt
python -m analysis validate-state
python -m analysis validate-cards
python -m unittest discover -s tests -v
```

## 状態ファイルと契約

JSON Schemaは`schemas/analysis-state.schema.json`（Draft 2020-12）です。
`schema_version: "1.2.1"`と`kind`で文書種別を指定します。
`analysis.state.read_state` / `write_state`はschemaと追加の整合性検査を実行します。
書き込みは一時ファイルからのatomic replaceです。同じ状態ファイルへの更新は単一writerで行ってください。
異なるwriterの同時更新を調停する機能はありません。

| ファイル（`data/analysis/`内） | 内容 |
| --- | --- |
| `event_ledger.json` | event_idごとの開催台帳・観測履歴 |
| `deck_classifications.json` | deck_codeごとの分類sidecar |
| `processed_sources.json` | 処理済みページ・動画・ファイルの記録 |
| `research_requests.json` | 調査依頼・状態・結果 |
| `forecast_log.json` | 予測内容・入力snapshot_ids・事後結果の記録 |
| `weekly_snapshots/<target_week>/<snapshot_id>/` | metadataと入力ファイルの実体 |

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
| `coverage_scope` | unknown / result_list / full_results / top8 / partial_results |
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
既存収集は台帳を自動更新しません。新たな観測の記録はこのAPIを使用します。

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
python -m analysis snapshot --target-week 2026-W40 --retrieved-at 2026-10-02T23:45:39+09:00 --event-id 1115295
python -m analysis verify-snapshot data/analysis/weekly_snapshots/2026-W40/<snapshot_id>
```

`target_week`はISO週です。`retrieved_at`は入力を実際に取得した時刻を呼び出し側が指定します。
`created_at`はスナップショットの作成時刻で、取得時刻とは分離します。日時は時差情報必須です。
対象event_id一覧は実際の分析対象を明示し、固定するcity_db内に存在することを検証します。
取得済みの全大会がその週の大会とは限らないので、自動で週や対象大会を推測しません。
metadataにはHEADのGit SHA、作業ツリー変更有無、元ファイル参照、コピー先、SHA256を保存します。
デフォルトでは既存3つのJSONと5つの状態ファイルをコピーします。
追加資料・分類器・カードマスターを使う場合は、それらも`--file`で指定してください。
`--file`を指定するとデフォルト一覧を置き換えるため、必要な入力をすべて列挙してください。
`data/city_db.json`は必須です。

同じ週でも一意なsnapshot_idの新しいディレクトリに保存します。
既存ディレクトリへの書き込みと`write_state`によるsnapshot metadata更新は拒否します。
元データが後日更新されても`inputs/`のコピーは保持され、再分析はこのコピーを参照します。
作業ツリーに変更がある場合、Git SHAだけでは入力を特定できないため、コピーとSHA256が入力版の根拠です。
metadataは最後に作成します。失敗でmetadataがないディレクトリは未完成として扱ってください。
検証は保存後の改変を検出しますが、外部からのファイル直接編集をOSで禁止するものではありません。

Workで検収された対象週・対象大会・入力版はこのリポジトリに提供されていないため、
初期実装で実分析用のスナップショットは捏造していません。
今後の各分析結果には`target_week`と`snapshot_id`を記録してください。

## 既存処理との関係と検証

既存workflowのDownload → Validate and update → Commitは変更していません。
追加ツールは自動収集経路に入らず、workflowのステージ対象も既存3ファイルのままです。
`collector/collect.py`の挙動も維持します。
unit testはworkflow内の既存Validate Pythonコードを読み取り、一時ディレクトリで
現データの合格・60枚違反の拒否・大会件数減少の拒否を検査します。
ネットワークアクセスやworkflowの手動実行は行いません。
