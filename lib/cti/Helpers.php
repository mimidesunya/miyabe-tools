<?php
/**
 * ストリームへデータを入出力するためのユーティリティです。
 * 
 * これらの関数は、ノンブロッキングI/Oに対しても与えられた(要求される)データを全て出力(入力)します。
 * 
 * 通常、プログラマがこのパッケージを直接使う必要はありません。
 * 
 * @package CTI
 * @subpackage Helpers
 */

/**
 * パケットの送信に使うバッファのサイズです。
 * 
 * @access private
 */
define ('CTI_BUFFER_SIZE', 1024);

/**
 * 32ビット数値をビッグインディアンで書き出します。
 * 
 * @param resource $fp ストリーム
 * @param int $a 数値
 * @return mixed 書き込んだバイト数
 * @access public
 */
function cti_utils_write_int(&$fp, $a) {
  $data = pack('N', $a);
  return _cti_write($fp, $data);
}

/**
 * 16ビット数値をビッグインディアンで書き出します。
 * 
 * @param resource $fp ストリーム
 * @param int $a 数値
 * @return mixed 書き込んだバイト数
 * @access public
 */
function cti_utils_write_short(&$fp, $a) {
  $data = pack('n', $a);
  return _cti_write($fp, $data);
}

/**
 * 64ビット数値をビッグインディアンで書き出します。
 * 
 * @param resource $fp ストリーム
 * @param int $a 数値
 * @return mixed 書き込んだバイト数
 * @access public
 */
function cti_utils_write_long(&$fp, $a) {
  $data = pack('NN', $a >> 32, $a & 0xFFFFFFFF);
  return _cti_write($fp, $data);
}

/**
 * 8ビット数値を書き出します。
 * 
 * @param resource $fp ストリーム
 * @param int $b 数値
 * @return mixed 書き込んだバイト数
 * @access public
 */
function cti_utils_write_byte(&$fp, $b) {
  $data = chr($b);
  return _cti_write($fp, $data);
}

/**
 * バイト数を16ビットビッグインディアンで書き出した後、バイト列を書き出します。
 * 
 * @param resource $fp ストリーム
 * @param string $b バイト列
 * @return mixed 書き込んだバイト数
 * @access public
 */
function cti_utils_write_bytes(&$fp, &$b) {
  $data = pack('n', strlen($b));
  $len = _cti_write($fp, $data);
  return _cti_write($fp, $b);
}

/**
 * バイト列を書き出します。
 * 
 * @param resource $fp ストリーム
 * @param string $data バイト列
 * @return mixed 書き込んだバイト数
 * @access private
 */
function _cti_write(&$fp, &$data) {
  for (;;) {
    $len = fwrite($fp, $data);
    if ($len === false) {
      throw new Exception('I/O Error');
    }
    if ($len >= strlen($data)) {
      return $len;
    }
    $data = substr($data, $len, strlen($data) - $len);
  }
}

/**
 * 16ビットビッグインディアン数値を読み込みます。
 * 
 * @param resource $fp ストリーム
 * @return mixed 数値、エラーであればfalse
 * @access public
 */
function cti_utils_read_short(&$fp) {
  $b = _cti_read($fp, 2);
  $a = unpack('nint', $b);
  return $a['int'];
}

/**
 * 32ビットビッグインディアン数値を読み込みます。
 * 
 * @param resource $fp ストリーム
 * @return mixed 数値、エラーであればfalse
 * @access public
 */
function cti_utils_read_int(&$fp) {
  $b = _cti_read($fp, 4);
  $a = unpack('Nint', $b);
  return $a['int'];
}

/**
 * 64ビットビッグインディアン数値を読み込みます。
 * 
 * @param resource $fp ストリーム
 * @return mixed 数値、エラーであればfalse
 * @access public
 */
function cti_utils_read_long(&$fp) {
  $b = _cti_read($fp, 4);
  $a = unpack('Nint', $b);
  $h = $a['int'];
  $b = _cti_read($fp, 4);
  $a = unpack('Nint', $b);
  $l = $a['int'];
  if ($h >> 31 != 0) {
    $h ^= 0xFFFFFFFF;
    $l ^= 0xFFFFFFFF;
    $b = ($h << 32) | $l; 
    $b = -($b + 1);
  }
  else {
    $b = ($h << 32) | $l; 
  }
  return $b;
}

/**
 * 8ビット数値を読み込みます。
 * 
 * @param resource $fp ストリーム
 * @return mixed 数値、エラーであればfalse
 * @access public
 */
function cti_utils_read_byte(&$fp) {
  $b = _cti_read($fp, 1);
  return ord($b);
}

/**
 * 16ビットビッグインディアン数値を読み込み、そのバイト数だけバイト列を読み込みます。
 * 
 * @param resource $fp ストリーム
 * @return mixed バイト列、エラーであればfalse
 * @access public
 */
function &cti_utils_read_bytes(&$fp) {
  $b = _cti_read($fp, 2);
  $a = unpack('nshort', $b);
  $len = $a['short'];
  $b = _cti_read($fp, $len);
  return $b;
}

/**
 * バイト列を読み込みます。
 * 
 * @param resource $fp ストリーム
 * @param int $len 要求されるバイト数
 * @return mixed バイト列、エラーであればfalse
 * @access private
 */
function &_cti_read(&$fp, $len) {
  $result = '';
  for (;;) {
    if ($len <= 0) {
      break;
    }
    $data = fread($fp, $len);
    if ($data === '' || $data === false) {
      throw new Exception('I/O Error');
    }
    $len -= strlen($data);
    $result .= $data;
  }
  return $result;
}
