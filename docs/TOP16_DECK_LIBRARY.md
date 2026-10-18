# Top16 60枚デッキライブラリ

2027-S1の公式シティリーグ結果について、Top16（順位1～16）のデッキを後から検索するための**読み取り専用の派生キャッシュ**です。

## データの保存先
- 全デッキの60枚原本：[\`data/city_decks.json\`](../data/city_decks.json)（デッキコード別）
- 大会、順位、デッキコード：[\`data/city_db.json\`](../data/city_db.json)
- ライブラリ入口：[\`data/deck_library/manifest.json\`](../data/deck_library/manifest.json)
- 各週の全Top16検索索引：\`data/deck_library/top16/W03.json\` など
- 各週の**ダダリン採用デッキの完全な60枚**：\`data/deck_library/dhelmise/W03.json\` など

上記の索引は従来の原本と重複して全てのカードを保存しません。全Top16の順位・デッキコードから、原本の60枚へ結合できます。例外として、ダダリン採用リストはチャットや外部集計ツールからすぐ読めるように60枚全件を小さい週単位ファイルへ複製します。Git履歴には世代ごとの変更が残ります。

## 抽出基準と注意事項
- W01は2026年9月21日～27日、以降月曜～日曜（JST）。全カテゴリ対象、順位1～16。
- デッキが未公開の場合、索引に\`deck_code: null\`を保存し60枚を補完・推定しません。
- ダダリンの判定は \`analysis/classification_cards.json\` に登録された **card_idと登録済みの正確なカード表示名（同一IDが他のカードと重複しない場合は正式な素の名称も許可）** の組合せです。単純な部分一致は使いません。新カードIDの追加は辞書更新が必要です。
- \`dhelmise_count >= 1\` ならダダリン採用。さらに\`ジュペッタ>=2、ダダリン>=3\`を満たす構築は\`banette_dhelmise_pattern: true\`とします。これは構造一致のフラグであり、必ずしも最終的なデッキ分類・勝率を意味しません。
- \`confirmed_top16_observation_event_count\`は公式からTop9-16が8枠以上確認され、当該観測行が格納された大会数です。未確認大会は完全取得とみなしません。Top16全大会網羅率とは異なります。
- 60枚の合計または原本SHA256が不一致なら**更新を停止**。既存のW01/W02 FINAL、snapshot、予測ログは一切変更しません。
- 元データはカードの収録番号違いで表示名が異なるため、**平均60枚の算出ではカードの論理同一性を先に定義する**必要があります。本工程はライブラリ化までです。

## 生成・検索
\`\`\`sh
python -m analysis.deck_library
python -m analysis.deck_library --query dhelmise --week W03
python -m analysis.deck_library --query dhelmise --week W03 --include-cards
python -m analysis.deck_library --query top16 --week W03
python -m unittest tests.test_deck_library -v
\`\`\`

GitHub Actions「Build Top16 deck library」は原本\`data/index.json\`が更新された時に自動で派生キャッシュを再生成し、手動起動にも対応。元データ自体の収集処理や対象大会は変更しません。

## ダダリンの構造別分類

デッキ内のポケモンを元に、互いに重複しない主分類タグを全リストに付与します。

- ヤバソチャ採用型：ヤバソチャを1枚以上含むもの。
- フーディン併用型：ヤバソチャなし・フーディンを1枚以上含むもの。
- ヤバソチャ・フーディン非採用型：両方含まないもの（チャデスのみの場合も含む）。

別軸でノココッチex併用型・通常ノココッチ型（exなし）・ノココッチ非採用の3タグを付けます。

- 週別60枚データ：`dhelmise/Wxx.json` の各行に `archetype_group` と `dunsparce_engine_group` を追加。
- 集計結果：`data/deck_library/dhelmise_groups.json`。元DBのSHA256と週単位件数を保持。
- 再生成：`python -m analysis.deck_library && python -m analysis.dhelmise_groups`。

デッキ分類は採用カードの構造的な分類であり、プレイスタイルや対戦勝率の認定ではありません。入賞枠を単位として集計し、同じデッキコードが別大会で使われた場合は別件です。
