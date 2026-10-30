# PostgreSQL continuity store

モデルが次回の作業で実際に読み直すための外部コンテキスト保存先です。保存しただけでモデルの会話へ注入されたとは扱いません。

```bash
bash operations/postgres/install.sh
bash operations/postgres/start.sh
source operations/postgres/activate.sh
```

PostgreSQL 17をDebianの署名済みパッケージからworkspaceへ展開します。初回は17.11で確認しました。データは`/workspace/.local/state/postgresql/data`、接続ソケットは`/tmp/chatgpt1027-postgresql-socket`、DB名は`chatgpt1027`です。永続データと一時的なIPCを分け、公開対象のworkspaceへUnixソケットを残しません。TCPでは待ち受けず、OSユーザー`agent`のpeer認証で使います。パスワードやAPIキーは不要です。

```bash
python operations/postgres/load_context.py \
  --records /workspace/shared/chatgpt1027/context-records.json \
  --artifact-root /workspace/langgraph/examples/cli_context_packets
bash operations/postgres/read_context.sh
bash operations/postgres/read_context.sh artifact/VERIFICATION.json
bash operations/postgres/backup.sh
```

`context-records.json`は私的な会話・引き継ぎ内容を置く外部ファイルです。各レコードは`key/kind/source/status/body`を持ち、statusでユーザー発言、保存した資料、実測、未確認を区別します。元の内容をJSONBで追記保存し、同じ内容の二重登録を避けます。

読み取りはDBに実際に問い合わせ、取得したレコードのhashをread receiptとして記録します。デフォルトでは方針・引き継ぎを返し、大きな資料はキーを指定して読みます。receiptはDBから取得した記録であり、モデルが内容を理解したことの証明ではありません。

論理バックアップは`/workspace/shared/chatgpt1027/*.dump`へ保存します。このフォルダーとPostgreSQLの実データはGitへ含めません。Google Driveへのアップロードは別の操作で、バックアップを作っただけでは転送済みになりません。

復元は稼働中のPostgreSQLで空の復元先DBを作成し、`pg_restore --single-transaction --dbname=<復元先> <dump>`を実行します。既存のDBへ上書きする手順は含めていません。環境のPublish前にも論理バックアップを取り、次の環境ではサービスを起動してから実際に読み直してください。
