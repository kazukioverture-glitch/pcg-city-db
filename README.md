# pcg-city-db

週次分析のデータ品質・状態管理基盤 v1.2.2 は、既存DB・Validateを維持し、収集と台帳を接続する追加ツールです。
仕様・使い方・検証方法は [docs/analysis-state.md](docs/analysis-state.md) を参照してください。


## FINAL回答 fast path

確定済み週の通常参照は `data/analysis/answer_index/current.json` から開始します。
`summary.json → decks.json → raw snapshot → external research` の順で必要なところまでだけ降り、
既存事実の回答のために全RAW再集計や全テストを実行しません。
詳細は [docs/ANSWER_FAST_PATH.md](docs/ANSWER_FAST_PATH.md) を参照してください。
