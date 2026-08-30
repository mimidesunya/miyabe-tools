<?php
namespace CTI;

/**
 * リソース出力クラスです。
 */
class ResourceOutput {
  private $fp;
  private $uri;
  public $missing = true;
  private $ob = false;
  
  public function __construct($fp, $uri) {
    $this->fp = $fp;
    $this->uri = $uri;
  }
  
  /**
   * リソース送信のための出力のバッファリングを有効にします。
   *
   * start,endは対となります。
   * 
   * @param array $opts リソースオプション('mimeType', 'encoding', 'length'というキーでデータ型、文字コード、長さを設定することができます。)
   */
  public function start($opts = array()) {
    $mimeType = isset($opts['mimeType']) ? $opts['mimeType'] : 'text/css';
    $encoding = isset($opts['encoding']) ? $opts['encoding'] : '';
    $length = isset($opts['length']) ? $opts['length'] : -1;
    cti_ctip_req_resource($this->fp, $this->uri, $mimeType, $encoding, $length);
    $this->missing = false;
    $this->ob = true;
    ob_start();
    ob_start(array($this, '_handler'), CTI_BUFFER_SIZE);
  }
  
  public function _handler($buffer) {
    for (;;) {
      $buff = substr($buffer, 0, CTI_BUFFER_SIZE);
      $len = strlen($buff);
      if ($len <= 0) {
        break;
      }
      $buffer = substr($buffer, $len);
      cti_ctip_req_write($this->fp, $buff);
    }
    return '';
  }
  
  /**
   * バッファの内容を送信し、リソース送信のためのバッファリングを終了します。
   *
   * start,endは対となります。
   */
  public function end() {
    if ($this->ob) {
      ob_end_clean();
      ob_end_clean();
      cti_ctip_req_eof($this->fp);
      $this->ob = false;
    }
  }
}
