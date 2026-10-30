# CLI Context Packets

CLIエージェントのコンテキスト収集を、元ソース一式を保持したパケットとしてLangGraphへ接続する実装です。推論先は既存ローカルAIのstdio。APIキーは追加しません。

モデルの継続用コンテキストを実際に保存・読み直すPostgreSQLの手順は[operations/postgres](operations/postgres/README.md)にあります。会話データ・DB本体・ダンプはこのコードリポジトリへ含めません。

## 入っているもの

| パケット | 保存範囲 | 実行経路 |
| --- | --- | --- |
| Aider | 取得コミットの全追跡ファイル・全テスト・依存宣言 | 元の`RepoMap`を直接呼ぶ。AST、参照グラフ、PageRank、予算選別、キャッシュ処理を保持 |
| Qwen Code | 同上 | 元の`searchGlobDirectory`、`FileDiscoveryService`、ignoreパーサ、`runRipgrep`、descriptor読み込みを呼ぶ |
| Continue | 同上 | 元の`BaseRetrievalPipeline`、FTSクラス、NoReranker/Reranker、RepoMapによるファイル選択、重複除去、code/basic chunker、Llama tokenizerとWASM parserを呼ぶ |
| Codex | 同上 | 元のRust文字列ライブラリを直接ビルド。分岐時の履歴継承ルールをPythonへ移植 |

`packets/*/source`は取得コミットの全ツリーです。`git archive`で展開した後、Git blobの元バイト列へ照合し、改行属性による変換も元へ揃えています。元テストや依存ファイルを抜いた縮約版にはしていません。`manifest.json`に取得元、固定コミット、各ファイルのSHA-256・実行属性・symlinkを記録しています。既存のチェックアウトも変更していません。

新しくGit cloneした際に上流の改行属性がWindows用ファイル1件をCRLFへ変換するため、installはその既知の改行変換だけを原本へ戻します。元manifestのSHA-256に一致する場合だけ復元し、期待値を書き換えて検証を通す処理は行いません。

ここで保存するパケットはソース配布単位です。Akashic Packet OSのkernel、DB、identity、正本化経路は変更していません。

## 起動

```bash
cd /workspace/langgraph/examples/cli_context_packets
bash scripts/install.sh
```

ローカルAIの既存コマンドを`config/local-stdio.json`の`model.command`へargv配列で設定します。空の配列は未接続を表し、別のモデルへ自動切替はしません。

```bash
uv run --frozen context-packets '調査したい内容' \
  --root /workspace/langgraph/libs/langgraph \
  --config config/local-stdio.json
```

モデルなしで実際の収集・検索・統合を確認する経路もあります。この場合は推論したとは記録しません。

```bash
uv run --frozen context-packets 'StateGraph conditional edges' \
  --root /workspace/langgraph/libs/langgraph/langgraph \
  --config config/offline.json
```

ワークフロー自体をstdioから使う場合は`uv run --frozen context-packets --stdio`。1行ごとに`{"task":"...","root":"...","model":{...},"settings":{...}}`を受け取り、`{"result":...}`または`{"error":...}`を返します。

## stdioの境界

`model.format`で以下を選べます。HTTPへの変換や新しいサーバーの常駐は不要です。

- `text`: CLIへ依頼を標準入力で渡し、標準出力を読み取る。構造化処理はJSON、`chat`だけは通常の文章を受け取る。
- `json`: 1回のCLI起動で1つのJSON依頼を渡す。出力はJSON値または`{"result":...}`。子プロセスにはEOFを送る。
- `mcp`: 公式Python SDKでstdio MCPのinitializeとtool callを行う。`model.tool`（初期値`generate`）、`model.argument`（初期値`request`）を既存ツールに合わせる。

モデル操作は`route`、`chat`、`rerank`、`embed`、`judge`、`synthesize`。`route`と`judge`にはJSON Schemaを渡します。`rerank`は候補ごとの有限スコア配列、`embed`は入力テキストごとの同一次元ベクトル配列です。埋め込み機能を持たないモデルでは分岐の`use_embeddings`をfalseにします。プロトコルと既存CLIの差は`context_packets/transport.py`だけで調整できます。

## LangGraphの実行

```text
prepare: Qwenで対象ファイル探索 → Aiderの構造マップ
plan: ローカルAIの分岐選択、または設定済みの分岐
  ├ collect_branch: Qwen検索 → Continueの取得・選別
  └ collect_branch: 別の検索条件・履歴・収集予算
merge: 全観測を保持して統合、Codexでモデル入力の予算処理
judge: 不足なら別条件で再探索、十分ならfinish
finish: ローカルAIの回答と出典、全収集結果、実行記録
```

分岐は`name/query/pattern/glob/fork_turns/n_final/rerank/use_embeddings/tool_calling_only`を持ちます。設定の`settings.branches`で固定すれば、route処理を外せます。`fork_turns`は元のCodexと同じく`none`、`all`、互換用の正の整数を受け付けます。各枝の履歴はdeep copyされ、兄弟の状態を書き換えません。

Continueの全文検索・ファイル読み取り・モデル呼び出し・埋め込みインデックスは、ホスト用の境界へ接続しています。SQLite FTS5で元のFTS検索SQLを実行します。LanceDBの永続化の代わりに、stdioから取得した埋め込みで今回の候補をcosine順位付けします。最近編集したファイルは`settings.recent_files`で相対パスを渡します。これはIDEの最近使用したファイル一覧を受け取る境界です。

最近使用したファイルの分割には元のContinueのcode/basic chunkerと元のLlama tokenizerを使います。構造を折り畳んだchunkは派生コンテキストとして、読み込んだ元ファイル全文は`source_documents`として保持します。収集ワーカーの対象はUTF-8のソース本文で、サイズ超過・バイナリ・非UTF-8・ルート外symlinkは`omitted`に理由を残します。PDF・画像などの元処理もソースパケット内に保持していますが、この接続の実行対象には含めていません。

元のContinueのBM25閾値は`-2.5`です。小さな候補集合では全件落ちることがあります。`settings.bm25_threshold`で調整でき、`config/offline.json`は小規模検証用に`0`を明示しています。元ソースの定数は変更せず、ワーカー内の設定として適用します。

`tool_calling_only`では元のContinueが読み取りツールを選びます。実行対象はread_file、grep_search、file_glob_search、ls、view_repo_map、view_subdirectory。ローカルで実行し、その観測と元のファイル本文を区別して保存します。

## 微調整する場所

- `config/local-stdio.json`: 既存CLI、プロトコル、ラウンド・ファイル・コンテキスト予算
- `context_packets/workflow.py`: 分岐、履歴継承、再探索、統合、終了条件
- `context_packets/transport.py`: 既存stdioプロトコルとの接続
- `bridges/continue-host.ts`: IDE、SQLite、埋め込み、モデルの実行環境
- `bridges/worker.ts`: 元の収集処理の呼び出しと読み取りツール
- `bridges/build.mjs`: 全import差し替え。結果は`dist/build-manifest.json`に記録
- `packets/*/source`: 元実装を調べる場所。変更すればverifyがそのファイル名で失敗するため、意図した移植変更を追跡できる

## 本文と検証

Qwenが読んだ全snippet、Continueが選んだ元chunk、構造マップ、実行した読み取りツールの結果、モデルへの依頼・応答を保存します。予算で切り詰めるのはモデルへ渡す`model_context`だけです。元本文は`evidence`と各枝の結果から消しません。ファイル観測にはパス・行・本文hash・ファイルhashを付け、取得後の変更や捏造された本文を拒否します。AIの評価と実行したツール出力は元本文とは別の観測として保持します。

結果は`var/runs/<内容のSHA-256>.json`へ保存します。同じ内容は同じファイルになり、異なる内容を上書きしません。このhashはこの配布物の実行記録用で、Packet OSの正本identityを発行するものではありません。

```bash
bash scripts/check.sh
```

検証は元のQwen・Continueの対象テスト、Aiderの重要ファイル判定テスト、元のCodex Rustテストと、実際の収集・並列分岐・再探索・stdio往復の統合テストです。全エージェントの全機能のテストを実行するものではありません。

`tests/fixtures/model.py`と`mcp_model.py`はプロトコル検証専用です。実モデルではなく、実際のローカルAIへの接続が成功したという証拠には使いません。現環境ではローカルAIの実コマンドは未設定です。

ライセンスは[LICENSE](LICENSE)、由来と実行境界は[NOTICE](NOTICE)を参照してください。
