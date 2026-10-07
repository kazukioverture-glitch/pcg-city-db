# Answer fast path

FINAL週の通常回答では、分析を再実行する前に回答インデックスを使う。

## 読み順

1. `data/analysis/answer_index/current.json`
2. `current.json` が指す `summary.json`
3. 一覧・未分類・大会別など行単位の確認が必要な場合のみ `decks.json`
4. 60枚の具体的な中身が必要な場合のみ immutable snapshot の raw inputs
5. DBで回答不能かつ結論への影響が大きい場合のみ外部調査

## 禁止する無駄

FINAL週の既存事実を答えるだけのために、全テスト、collector、全RAW再集計、Web検索を実行しない。
`summary.json` / `decks.json` の provenance が質問対象の週・snapshot・classifier と一致していれば再利用する。

## 更新

`python -m analysis materialize-answer-index --snapshot ...` で生成する。
FINAL snapshotだけを対象とし、collector DB・snapshot・forecastは変更しない。
classifier hash が変われば別 generation directory になる。

## 自動更新

main に `data/analysis/weekly_snapshots/<Wxx>/FINAL/**` が追加された場合、
`.github/workflows/materialize-answer-index.yml` が最新FINALを選び、
前週7日間をTREND比較期間として answer index を再生成する。

30分collector更新では起動しない。answer index自身のcommitでも再起動しない。

## 回答エージェント向け固定ルール

FINAL週について既存の事実を回答する場合、最初に `current.json` を確認する。
質問対象と week/snapshot/classifier が一致する限り、summary/decksを正本の派生成果物として使う。
RAW・全テスト・Web調査へ降りるのは、その成果物だけでは回答できない場合に限定する。
