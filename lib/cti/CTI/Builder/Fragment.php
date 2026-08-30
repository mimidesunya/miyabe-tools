<?php
namespace CTI\Builder;

/**
 * フラグメントを表すクラスです。
 */
class Fragment {
  public $id;
  public $prev = null;
  public $next = null;
  private $length = 0;
  private $buffer = '';
  private $segments;
  private $segLen;
    
  public function __construct($id) {
    $this->id = $id;
  }

  /**
   * フラグメントにデータを書き込みます。
   * 
   * @param resource $tempFile 一時ファイル
   * @param int $onMemory メモリ上に置かれたデータの合計サイズ
   * @param int $segment セグメント番号シーケンス
   * @param string $bytes データ
   * @return int 書き込んだバイト数
   */
  public function write(&$tempFile, &$onMemory, &$segment, &$bytes) {
    $len = strlen($bytes);
    if (!isset($this->segments) &&
        ($this->length + $len) <= CTI_CTIP_FRG_MEM_SIZE &&
        ($onMemory + $len) <= CTI_CTIP_ON_MEMORY) {
      $this->buffer .= $bytes;
      $onMemory += $len;
    }
    else {
      if (isset($this->buffer)) {
        $wlen = $this->raf_write($tempFile, $segment, $this->buffer);
        $onMemory -= $wlen;
        unset($this->buffer);
      }
      $len = $this->raf_write($tempFile, $segment, $bytes);
    }
    $this->length += $len;
    return $len;
  }

  /**
   * 一時ファイルに書き込みます。
   *
   * @param resource $tempFile 一時ファイル
   * @param int $segment セグメント番号シーケンス
   * @param string $bytes データ
   * @return int 書き込んだバイト数
   */
  private function raf_write(&$tempFile, &$segment, $bytes) {
    if (!isset($this->segments)) {
      $this->segments = array($segment++);
      $this->segLen = 0;
    }
    $written = 0;
    while (($len = strlen($bytes)) > 0) {
      if ($this->segLen == CTI_CTIP_SEGMENT_SIZE) {
        $this->segments[] = $segment++;
        $this->segLen = 0;
      }
      $seg = $this->segments[count($this->segments) - 1];
      $wlen = min($len, CTI_CTIP_SEGMENT_SIZE - $this->segLen);
      $wpos = $seg * CTI_CTIP_SEGMENT_SIZE + $this->segLen;
      if (fseek($tempFile, $wpos) === -1) {
        throw new \Exception("I/O Error");
      }
      if (($wlen = fwrite($tempFile, $bytes, $wlen)) === false) {
        throw new \Exception("I/O Error");
      }
      $this->segLen += $wlen;
      $written += $wlen;
      $bytes = substr($bytes, $wlen);
    }
    return $written;
  }

  /**
   * フラグメントの内容を吐き出して、フラグメントを破棄します。
   * 
   * @param resource $tempFile 一時ファイル
   * @param mixed $out 出力先ストリーム(resource),出力先変数(string),または標準出力であればnull。
   */
  public function flush(&$tempFile, &$out) {
    if (!isset($this->segments)) {
      _cti_output($out, $this->buffer);
      unset($this->buffer);
    }
    else {
      $segcount = count($this->segments);
      for ($i = 0; $i < $segcount - 1; ++$i) {
        $seg = $this->segments[$i];
        $rpos = $seg * CTI_CTIP_SEGMENT_SIZE;
        if (fseek($tempFile, $rpos) == -1) {
          throw new \Exception("I/O Error");
        }
        $buff = \_cti_read($tempFile, CTI_CTIP_SEGMENT_SIZE);
        _cti_output($out, $buff);
      }
      $seg = $this->segments[$segcount - 1];
      $rpos = $seg * CTI_CTIP_SEGMENT_SIZE;
      if (fseek($tempFile, $rpos) == -1) {
        throw new \Exception("I/O Error");
      }
      $buff = \_cti_read($tempFile, $this->segLen);
      _cti_output($out, $buff);
    }
  }
}
