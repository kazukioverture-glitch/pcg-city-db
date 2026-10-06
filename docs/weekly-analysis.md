# 週次分析エンジン v1

## 目的

W02 FINAL以降の固定snapshotから、資料化前のテキスト/JSON分析を再計算する。
収集DB・FINAL入力は変更しない。CURRENT_WEEKとTREND、全国と地域、事実と外部考察を分離する。

## 実装範囲

- Top8 / Top4 / Top2 / 優勝の構成比
- Top8→Top4、Top4→Top2、Top2→優勝、Top8→優勝のconversion
- conversionのWilson 95%信頼区間、分母20未満の運用上の少数標本警告
- 親アーキタイプと複数派生タグの分離
- 採用リスト数、採用率、採用時平均枚数
- 全国と指定都道府県の分離。地域判定は開催店舗 `venue_prefecture` のみ
- 明示した前週期間とのTREND。過去週値をCURRENT_WEEKへ混ぜない
- 独立分析済み外部sourceの結合（作者考察を事実化しない）
- 自動監査とMarkdown出力

「使用率」は出さない。`top8_share` は捕捉したTop8行の構成比、カード採用率は60枚リスト判明行を分母にする。

## W02 FINALの注意

既存 W02 FINAL `c2931ef42b944a3e86536742089d0c3f` は分類投入前に固定されており、
snapshot内の `deck_classifications.json` は空である。既存FINALは変更しない。

週次エンジンはsidecarにレコードがある場合だけそれを使い、レコードがないdeck_codeは
**snapshot内の60枚リスト**を現在の保守的分類器で再判定する。この場合は
`source=derived_post_snapshot` と記録し、レポートには分類器versionと辞書SHA256を残す。
未対応デッキは `未分類` のまま分母に残し、推測で親名を埋めない。

## 実行例

```bash
python -m analysis weekly-report \
  --snapshot data/analysis/weekly_snapshots/W02/FINAL/c2931ef42b944a3e86536742089d0c3f \
  --category オープン \
  --region 愛知県 \
  --compare-start 2026-09-23 \
  --compare-end 2026-09-29 \
  --output-json .tmp/W02-final.json \
  --output-md .tmp/W02-final.md
```

W01比較はW02 FINAL snapshotに保存されたcity dataを同一時点で再計算する。
これは「W01当時に固定したsnapshot」ではないため、TRENDには
`historical_basis=recomputed_from_same_FINAL_snapshot_captured_city_data` を明記する。

## 採用カード

全カードの自動集約は `(card_id, exact display_name)` 単位で行い、別収録を勝手に同一視しない。
監視カードは `watch-cards-v1` の明示辞書を指定した場合だけ論理カード単位で集計する。

```json
{
  "version": "watch-cards-v1",
  "cards": [
    {
      "label": "シェイミ",
      "identities": [
        {"card_id": "verified-id", "name": "DB内の完全一致表示名"}
      ]
    }
  ]
}
```

候補ID探索は以下。これは候補列挙だけで、自動同一視しない。

```bash
python -m analysis discover-cards --snapshot <FINAL> \
  --name シェイミ --name ミネズミ --name ロケット団のフリーザー
```

## 外部動画・記事

動画はDB照合より先に独立分析し、`record-source-analysis` で保存する。
保存済み `processed` レコードは不変。DBとの一致/相違はsourceレコードへ後書きせず、
週次レポート側で扱う。

必須の独立分析項目は、公開日、対象週、確認できた事実、作者の考察、その根拠、
予測、注意事項、追跡シグナル、言語/翻訳、映像確認有無。解析不能は空配列とcaveatで表現し、
推測補完しない。

## 監査

FAIL:
- CURRENT_WEEK / TRENDの混在
- `usage_rate` / 「使用率」キー
- 地域判定が `venue_prefecture` 以外
- conversionで分母があるのに95%CIがない

WARN:
- 未分類行
- 監視カードexact ID辞書未設定
- 対象週の外部考察未結合

分母20未満は運用上の警告閾値であり、統計的有効/無効の境界ではない。

比較期間の逆転やCURRENT_WEEKとの日付重複は入力時に拒否し、レポート監査でもFAILとする。
Markdownにも分類provenance・辞書SHA256・過去週の再計算根拠を表示する。
全国と地域それぞれの全段階構成比、全conversionと95%CI・少数標本警告、
派生タグ、上位20 exactカードidentityの採用指標、TRENDを掲載する。
