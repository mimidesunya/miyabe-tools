<?php
/**
 * ドキュメント変換サーバーに接続するドライバの窓口関数です。
 * 
 * @package CTI
 * @example ../examples/output-stdout.php 結果を標準出力に出力する。
 * @example ../examples/output-file.php 結果をファイルに出力する。
 * @example ../examples/output-resource.php 結果をリソースに出力する。
 * @example ../examples/output-dir.php 結果を複数の画像としてディレクトリに出力する。
 * @example ../examples/output-var.php 結果を変数に出力する。
 * @example ../examples/message-func.php エラー等のメッセージを表示する。
 * @example ../examples/progress-func.php 処理の進行状況を表示する。
 * @example ../examples/reset.php 同じセッションで何度も処理をする。
 * @example ../examples/resolver.php サーバーからの要求に応じてリソースを送る。
 * @example ../examples/output-var.php 結果を変数に出力する。
 * @example ../examples/server-info.php サーバーの情報を取得する。
 * @example ../examples/server-resource.php サーバーから文書にアクセスして変換する。
 */

use CTI\Driver;

/**
 * 指定されたURIに接続するためのドライバを返します。
 * 
 * @param string $uri 接続先アドレス。
 * @return Driver
 */
function cti_get_driver($uri) {
  return new Driver();
}

/**
 * 指定されたURIに接続し、セッションを返します。
 * 
 * @param string $uri 接続先アドレス。
 * @param array|null $options 接続オプション。
 * @return \CTI\Session
 */
function cti_get_session($uri, $options = null) {
  $driver = cti_get_driver($uri);
  return $driver->get_session($uri, $options);
}
