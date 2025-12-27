# Miyabe Tools

選挙ポスター掲示場の設置・撤去などの作業状況をリアルタイムで共有・管理するためのツールです。
Docker Compose、Nginx、PHP-FPM、および SQLite を使用して動作します。

## 主な機能

- LINE ログイン連携による作業者認証
- 地図上での掲示場位置の確認と作業ステータスの更新
- 自治体ごとの掲示場データ管理

- Docker
- Docker Compose

## 始め方

1.  このリポジトリをクローンします。
2.  設定ファイルを作成します。
    ```bash
    cp config.example.json data/config.json
    ```
    `data/config.json` を開き、LINE Developers コンソールから取得した `CHANNEL_ID` や `CHANNEL_SECRET` を設定してください。

3.  以下のコマンドを実行してサービスを開始します。
    ```bash
    docker-compose up -d
    ```

4.  ブラウザを開き、[http://localhost:8301](http://localhost:8301) にアクセスします。

## プロジェクト構成

- `docker-compose.yml`: Nginx と PHP サービスを定義します。
- `nginx/default.conf`: Nginx の設定ファイルです。
- `app/`: 公開ディレクトリ（PHP, HTML, CSS, JS）。
- `lib/`: 非公開ライブラリ・設定ファイル。
- `data/`: SQLite データベースなどの永続データ（Git 除外）。
- `tools/`: データベース初期化やマイグレーション用のスクリプト。

## 開発

`app/` ディレクトリはコンテナ内の `/var/www/html` にバインドマウントされています。`app/` 内のファイルに加えた変更は、即座にブラウザに反映されます。
