<?php
/**
 * CTIP2の低レベルの部分を扱います。
 * 
 * 通常、プログラマがこのパッケージを直接使う必要はありません。
 * 
 * @package CTI
 * @subpackage CTIP2
 */

define ('CTI_CTIP_REQ_PROPERTY', 0x01);
define ('CTI_CTIP_REQ_START_MAIN', 0x02);
define ('CTI_CTIP_REQ_SERVER_MAIN', 0x03);
define ('CTI_CTIP_REQ_CLIENT_RESOURCE', 0x04);
define ('CTI_CTIP_REQ_CONTINUOUS', 0x05);
define ('CTI_CTIP_REQ_DATA', 0x11);
define ('CTI_CTIP_REQ_START_RESOURCE', 0x21);
define ('CTI_CTIP_REQ_MISSING_RESOURCE', 0x22);
define ('CTI_CTIP_REQ_EOF', 0x31);
define ('CTI_CTIP_REQ_ABORT', 0x32);
define ('CTI_CTIP_REQ_JOIN', 0x33);
define ('CTI_CTIP_REQ_RESET', 0x41);
define ('CTI_CTIP_REQ_CLOSE', 0x42);
define ('CTI_CTIP_REQ_SERVER_INFO', 0x51);

define ('CTI_CTIP_RES_START_DATA', 0x01);
define ('CTI_CTIP_RES_BLOCK_DATA', 0x11);
define ('CTI_CTIP_RES_ADD_BLOCK', 0x12);
define ('CTI_CTIP_RES_INSERT_BLOCK', 0x13);
define ('CTI_CTIP_RES_MESSAGE', 0x14);
define ('CTI_CTIP_RES_MAIN_LENGTH', 0x15);
define ('CTI_CTIP_RES_MAIN_READ', 0x16);
define ('CTI_CTIP_RES_DATA', 0x17);
define ('CTI_CTIP_RES_CLOSE_BLOCK', 0x18);
define ('CTI_CTIP_RES_RESOURCE_REQUEST', 0x21);
define ('CTI_CTIP_RES_EOF', 0x31);
define ('CTI_CTIP_RES_ABORT', 0x32);
define ('CTI_CTIP_RES_NEXT', 0x33);

/**
 * メモリ上のフラグメントの最大サイズです。
 * 
 * フラグメントがこの大きさを超えるとディスクに書き込みます。
 * 
 * @access private
 */
define ('CTI_CTIP_FRG_MEM_SIZE', 256);

/**
 * メモリ上に置かれるデータの最大サイズです。
 * 
 * メモリ上のデータがこのサイズを超えると、
 * CTI_CTIP_FRG_MEM_SIZEとは無関係にディスクに書き込まれます。
 * 
 * @access private
 */
define ('CTI_CTIP_ON_MEMORY', 1024 * 1024);

/**
 * 一時ファイルのセグメントサイズです。
 *
 * @access private
 */
define ('CTI_CTIP_SEGMENT_SIZE', 8192);

/**
 * セッションを開始します。
 * 
 * @param resource $fp ストリーム
 * @param string $encoding 通信に用いるエンコーディング
 * @access public
 */
function cti_ctip_connect(&$fp, $encoding) {
  $err = fwrite($fp, "CTIP/2.0 $encoding\n");
  if ($err === false) {
    throw new Exception('I/O Error');
  }
}

/**
 * サーバー情報を要求します。
 * 
 * @param resource $fp ストリーム
 * @param string $uri URI
 */
function cti_ctip_req_server_info(&$fp, $uri) {
  $payload = 1 + 2 + strlen($uri);
  cti_utils_write_int($fp, $payload);
  cti_utils_write_byte($fp, CTI_CTIP_REQ_SERVER_INFO);
  cti_utils_write_bytes($fp, $uri);
  if (fflush($fp) === false) {
    throw new Exception('I/O Error');
  }
}

/**
 * サーバーからクライアントのリソースを要求するモードを切り替えます。
 * 
 * @param resource $fp ストリーム
 * @param int $mode 0=off, 1=on
 */
function cti_ctip_req_client_resource(&$fp, $mode) {
  $payload = 2;
  cti_utils_write_int($fp, $payload);
  cti_utils_write_byte($fp, CTI_CTIP_REQ_CLIENT_RESOURCE);
  cti_utils_write_byte($fp, $mode);
  if (fflush($fp) === false) {
    throw new Exception('I/O Error');
  }
}

/**
 * 複数の結果を結合するモードを切り替えます。
 * 
 * @param resource $fp ストリーム
 * @param int $mode 0=off, 1=on
 */
function cti_ctip_req_continuous(&$fp, $mode) {
  $payload = 2;
  cti_utils_write_int($fp, $payload);
  cti_utils_write_byte($fp, CTI_CTIP_REQ_CONTINUOUS);
  cti_utils_write_byte($fp, $mode);
  if (fflush($fp) === false) {
    throw new Exception('I/O Error');
  }
}

/**
 * リソースの不存在を通知します。
 * 
 * @param resource $fp ストリーム
 * @param string $uri URI
 */
function cti_ctip_req_missing_resource(&$fp, $uri) {
  $payload = 1 + 2 + strlen($uri);
  cti_utils_write_int($fp, $payload);
  cti_utils_write_byte($fp, CTI_CTIP_REQ_MISSING_RESOURCE);
  cti_utils_write_bytes($fp, $uri);
  if (fflush($fp) === false) {
    throw new Exception('I/O Error');
  }
}

/**
 * 状態のリセットを要求します。
 * 
 * @param resource $fp ストリーム
 */
function cti_ctip_req_reset(&$fp) {
  $payload = 1;
  cti_utils_write_int($fp, $payload);
  cti_utils_write_byte($fp, CTI_CTIP_REQ_RESET);
  if (fflush($fp) === false) {
    throw new Exception('I/O Error');
  }
}

/**
 * 変換処理の中断を要求します。
 * 
 * @param resource $fp ストリーム
 * @param int $mode 0=生成済みのデータを出力して中断, 1=即時中断
 */
function cti_ctip_req_abort(&$fp, $mode) {
  $payload = 2;
  cti_utils_write_int($fp, $payload);
  cti_utils_write_byte($fp, CTI_CTIP_REQ_ABORT);
  cti_utils_write_byte($fp, $mode);
  if (fflush($fp) === false) {
    throw new Exception('I/O Error');
  }
}

/**
 * 変換結果を結合します。
 * 
 * @param resource $fp ストリーム
 */
function cti_ctip_req_join(&$fp) {
  $payload = 1;
  cti_utils_write_int($fp, $payload);
  cti_utils_write_byte($fp, CTI_CTIP_REQ_JOIN);
  if (fflush($fp) === false) {
    throw new Exception('I/O Error');
  }
}

/**
 * 終了を通知します。
 * 
 * @param resource $fp ストリーム
 * @access public
 */
function cti_ctip_req_eof(&$fp) {
  $payload = 1;
  cti_utils_write_int($fp, $payload);
  cti_utils_write_byte($fp, CTI_CTIP_REQ_EOF);
  if (fflush($fp) === false) {
    throw new Exception('I/O Error');
  }
}

/**
 * プロパティを送ります。
 * 
 * @param resource $fp ストリーム
 * @param string $name 名前
 * @param string $value 値
 * @access public
 */
function cti_ctip_req_property(&$fp, $name, $value) {
  $payload = strlen($name) + strlen($value) + 5;
  cti_utils_write_int($fp, $payload);
  cti_utils_write_byte($fp, CTI_CTIP_REQ_PROPERTY);
  cti_utils_write_bytes($fp, $name);
  cti_utils_write_bytes($fp, $value);
  if (fflush($fp) === false) {
    throw new Exception('I/O Error');
  }
}

/**
 * サーバー側データの変換を要求します。
 * 
 * @param resource $fp ストリーム
 * @param string $uri URI
 */
function cti_ctip_req_server_main(&$fp, $uri) {
  $payload = strlen($uri) + 3;
  cti_utils_write_int($fp, $payload);
  cti_utils_write_byte($fp, CTI_CTIP_REQ_SERVER_MAIN);
  cti_utils_write_bytes($fp, $uri);
  if (fflush($fp) === false) {
    throw new Exception('I/O Error');
  }
}

/**
 * リソースの開始を通知します。
 * 
 * @param resource $fp ストリーム
 * @param string $uri URI
 * @param string $mimeType MIME型
 * @param string $encoding エンコーディング
 * @param int $length 長さ
 * @access public
 */
function cti_ctip_req_resource(&$fp, $uri, $mimeType = 'text/css', $encoding = '', $length = -1) {
  $payload = strlen($uri) + strlen($mimeType) + strlen($encoding) + 7 + 8;
  cti_utils_write_int($fp, $payload);
  cti_utils_write_byte($fp, CTI_CTIP_REQ_START_RESOURCE);
  cti_utils_write_bytes($fp, $uri);
  cti_utils_write_bytes($fp, $mimeType);
  cti_utils_write_bytes($fp, $encoding);
  cti_utils_write_long($fp, $length);
  if (fflush($fp) === false) {
    throw new Exception('I/O Error');
  }
}

/**
 * 本体の開始を通知します。
 * 
 * @param resource $fp ストリーム
 * @param string $uri URI
 * @param string $mimeType MIME型
 * @param string $encoding エンコーディング
 * @param int $length 長さ
 * @access public
 */
function cti_ctip_req_start_main(&$fp, $uri, $mimeType = 'text/html', $encoding = '', $length = -1) {
  $payload = strlen($uri) + strlen($mimeType) + strlen($encoding) + 7 + 8;
  cti_utils_write_int($fp, $payload);
  cti_utils_write_byte($fp, CTI_CTIP_REQ_START_MAIN);
  cti_utils_write_bytes($fp, $uri);
  cti_utils_write_bytes($fp, $mimeType);
  cti_utils_write_bytes($fp, $encoding);
  cti_utils_write_long($fp, $length);
  if (fflush($fp) === false) {
    throw new Exception('I/O Error');
  }
}

/**
 * データを送ります。
 * 
 * @param resource $fp ストリーム
 * @param string $b データ
 * @param int $len データの長さ
 * @access public
 */
function cti_ctip_req_write(&$fp, &$b, $len = -1) {
  if ($len == -1) {
    $len = strlen($b);
  }
  else {
    $len = min(strlen($b), $len);
  }
  $payload = $len + 1;
  cti_utils_write_int($fp, $payload);
  cti_utils_write_byte($fp, CTI_CTIP_REQ_DATA);
  if (fwrite($fp, $b, $len) === false) {
    throw new Exception('IO Error');
  }
  if (fflush($fp) === false) {
    throw new Exception('I/O Error');
  }
}

/**
 * 通信を終了します。
 * 
 * @param resource $fp ストリーム
 */
function cti_ctip_req_close(&$fp) {
  $payload = 1;
  cti_utils_write_int($fp, $payload);
  cti_utils_write_byte($fp, CTI_CTIP_REQ_CLOSE);
  if (fflush($fp) === false) {
    throw new Exception('I/O Error');
  }
}

/**
 * 次のレスポンスを取得します。
 * 
 * レスポンス(array)には次のデータが含まれます。
 * 
 * - 'type' レスポンスタイプ
 * - 'anchorId' 挿入する場所の直後のフラグメントID
 * - 'level' エラーレベル
 * - 'error' エラーメッセージ
 * - 'id' 断片ID
 * - 'progress' 処理済バイト数
 * - 'bytes' データのバイト列
 * 
 * @param resource $fp ストリーム
 * @return array レスポンス
 * @access public
 */
function cti_ctip_res_next(&$fp) {
  $payload = cti_utils_read_int($fp);
  $type = cti_utils_read_byte($fp);
  
  switch ($type) {
      case CTI_CTIP_RES_ADD_BLOCK:
      case CTI_CTIP_RES_EOF:
      case CTI_CTIP_RES_NEXT:
      return array(
        'type' => $type,
      );
      
      case CTI_CTIP_RES_START_DATA:
      $uri = cti_utils_read_bytes($fp);
      $mime_type = cti_utils_read_bytes($fp);
      $encoding = cti_utils_read_bytes($fp);
      $length = cti_utils_read_long($fp);
      return array(
        'type' => $type,
        'uri' => $uri,
        'mime_type' => $mime_type,
        'encoding' => $encoding,
        'length' => $length
      );
      
      case CTI_CTIP_RES_MAIN_LENGTH:
      case CTI_CTIP_RES_MAIN_READ:
      $length = cti_utils_read_long($fp);
      return array(
        'type' => $type,
        'length' => $length
      );
      
      case CTI_CTIP_RES_INSERT_BLOCK:
      case CTI_CTIP_RES_CLOSE_BLOCK:
      $block_id = cti_utils_read_int($fp);
      return array(
        'type' => $type,
        'block_id' => $block_id
      );
      
      case CTI_CTIP_RES_MESSAGE:
      $code = cti_utils_read_short($fp);
      $payload -= 1 + 2;
      $message = cti_utils_read_bytes($fp);
      $payload -= 2 + strlen($message);
      $args = array();
      while ($payload > 0) {
        $arg = cti_utils_read_bytes($fp);
        $payload -= 2 + strlen($arg);
        $args[] = $arg;
      }
      return array(
        'type' => $type,
        'code' => $code,
        'message' => &$message,
        'args' => &$args
      );
      
      case CTI_CTIP_RES_BLOCK_DATA:
      $length = $payload - 5;
      $block_id = cti_utils_read_int($fp);
      $bytes = _cti_read($fp, $length);
      return array(
        'type' => $type,
        'block_id' => $block_id,
        'bytes' => &$bytes,
        'length' => $length
      );
      
      case CTI_CTIP_RES_DATA:
      $length = $payload - 1;
      $bytes = _cti_read($fp, $length);
      return array(
        'type' => $type,
        'bytes' => &$bytes,
        'length' => $length
      );
      
      case CTI_CTIP_RES_RESOURCE_REQUEST:
      $uri = cti_utils_read_bytes($fp);
      return array(
        'type' => $type,
        'uri' => $uri
      );
      
      case CTI_CTIP_RES_ABORT:
      $mode = cti_utils_read_byte($fp);
      $code = cti_utils_read_short($fp);
      $payload -= 1 + 1 + 2;
      $message = cti_utils_read_bytes($fp);
      $payload -= 2 + strlen($message);
      $args = array();
      while ($payload > 0) {
        $arg = cti_utils_read_bytes($fp);
        $payload -= 2 + strlen($arg);
        $args[] = $arg;
      }
      return array(
        'type' => $type,
        'mode' => $mode,
        'code' => $code,
        'message' => &$message,
        'args' => &$args
      );
      
      default:
      throw new Exception("Bad response type:$type");
  }
}
