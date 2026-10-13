# 定期収集の運用

## 取得元とスケジュール

公式API取得はAndroid/Termuxが担当する。GitHub ActionsはTermux配信を受信・検証する。GitHubでURL未設定の場合は安全停止し、公式直接取得へ戻さない。Androidは `TERMUX_BASE_URL` を解除して公式取得する。

ActionsはJST 18:00、20:00、22:00、23:00、翌08:00の1日5回。Androidの任意ループは07:30、17:30、19:30、21:30、22:30。定刻実行・Android再起動後の自動復帰・省電力制限下の継続は保証しない。

URLの優先順位はRepository Variable `CITY_TERMUX_BASE_URL` → 同名Secret → 移行用の現行URL。Settings → Secrets and variables → Actions → Variablesから変更でき、コードpushも臨時同期も不要。Quick TunnelのURLは再起動で変わる。設定登録を確認するまでfallbackを消さない。旧 `TERMUX_BASE_URL` 設定を誤って再有効化しないよう、新しい設定名を使用する。Pythonプロセスへは従来通り `TERMUX_BASE_URL` として渡す。新規サービスは使用しない。

## 検証と保存

`python -m collector.sync` が以下を担当する。

1. OSの排他ロックを取得する。二重実行は待たず失敗し、進行中の作業領域を消さない。プロセス終了でロックは解放される。
2. 前回の保存中断があれば `.tmp/publication.pending` の検証済みバックアップから旧ファイルを戻す。復旧が済むまで通常収集へ進まない。バックアップ不整合は停止する。
3. TermuxのDB2ファイルを同時取得する。完全なJSONだけ採用。HTTP/1.1、各ファイル最大540秒・3回。401/403/404/429は1回で止め、次の定期実行を待つ。404から開催・中止・公開件数を推定しない。
4. 片側欠損・不正な60枚リスト・参照欠落を拒否する。識別子と時刻でマージし、保存済みイベント・結果・デッキの減少や後退を防ぐ。配信元の鮮度とマージ後DBの鮮度は別記録。
5. 台帳・indexの件数/SHA256・スキーマを検証する。同じ配信内容・状態・大会属性の再受信は観測履歴へ重複追加しない。取得試行そのものは毎回の診断artifactに残す。既存の観測履歴は削除しない。汎用 `sync_ledger` は従来通り、重複抑制はcollectorの明示指定時だけ。
6. 変更ファイルだけ保存する。変更前のバイト列とSHA256を永続ジャーナルへ保存してから置換。通常例外はロールバックし、強制終了時は次の起動で復旧する。`.tmp/publication.pending` を手作業で消さない。
7. 成功・差分ありの場合のみ4ファイルを一括commit/pushする。push競合はforceしない。次の定期同期で再取得・マージする。

複数パスの同時読み取り原子性は保証しない。Android HTTP配信中の世代混在、端末電源断・ストレージ故障まで保証する仕組みではない。受信側の検証で拒否し、完全な世代の配信については残留リスクとして扱う。

Actionsの収集concurrencyは `cancel-in-progress: false` を維持。Analysis CIだけは同じPR/branchの古い検査を中断し、最新版を検査する。

## 成功の意味

- `termux_accepted`：転送後のDB候補を採用できた。
- `termux_source_health`：受信した配信元そのものの更新日時・24時間経過判定。
- `source_updated_at` 等：マージ後DBの情報。配信が最新であることとは別。
- `official_batch_complete`：この実行で選んだ公式取得バッチに報告済みエラー・繰越デッキがない。全大会取得完了ではない。
- `official_complete=false` / `result_coverage_status=not_verified`：収集だけでは全開催台帳との網羅性を認定しない。
- `final_status=not_evaluated`：監査とFINAL確定は別工程。
- `published=true`：検証済み候補の保存処理が成功（変更なしを含む）。Git push完了の意味ではない。
- `changed_files` / `db_bytes_written`：本番保存先へ実際に置換したファイルとバイト数。作業領域・バックアップのI/Oは含まない。

## 診断・復旧・検証

`sync-diagnostics-<run_id>` に同期報告、ダウンロード試行、取得できた公式監査を14日保存する。ダウンロード診断は応答本文バイト数・所要秒を含む。公開DBや選手情報の全件ZIPは作らない。

403/429は繰り返し手動実行しない。配信404ならAndroid側のHTTPサーバーとトンネルを確認し、URL変更時のみVariableを更新する。古い配信の再受信では鮮度は改善しない。必要時のみAndroidで `bash scripts/termux_refresh.sh once`。シェル側の旧mkdirロックが強制終了で残る場合は、稼働プロセスがないことを確認してから `.tmp/termux-refresh.lock` だけを `rmdir` する（publicationジャーナルは消さない）。

開発時の小さな検査（模擬データ・HTTPモックのみ）:

```sh
PYTHONPATH=tests:. python -m unittest test_transaction_recovery test_v122.CollectionTests test_sync_resilience.DownloadTests test_termux_source_freshness test_official_source_delegation -v
```

統合検収はAnalysis CIと同じ `python -m unittest discover -s tests -v`、`python -m analysis validate-state`、旧W02 FINALスモーク・回答インデックス生成・カード候補検査。旧FINAL/予測を変更せず、出力は `.tmp/analysis-ci` に限定する。
