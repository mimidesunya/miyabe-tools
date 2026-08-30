<?php
namespace CTI\Builder;

/**
 * 標準出力、ストリーム、変数のいずれかに出力します。
 *
 * @param mixed $out 出力先
 * @param string $buff データ
 * @access private
 */
function _cti_output(&$out, &$buff) {
  if ($out === null || $out === FALSE) {
    //標準出力
    echo $buff;
    $out = FALSE;
  }
  elseif (is_resource($out)) {
    //ストリーム
    if (fwrite($out, $buff) === false) {
      throw new \Exception("I/O Error");
    }
  }
  else {
    //変数
    $out .= $buff;
  }
}

/**
 * ストリームに出力するビルダーです。
 */
class StreamBuilder implements Builder {
  protected $out;
  protected $tempFile;
  protected $frgs = array();
  protected $first = null;
  protected $last = null;
  protected $onMemory = 0;
  protected $length = 0;
  protected $segment = 0;
  
  public function __construct(&$out = null) {
    $this->tempFile = tmpfile();
    $this->out =& $out;
  }

  public function add_block() {
    $id = count($this->frgs);
    $frg = new Fragment($id);
    $this->frgs[$id] =& $frg;
    if ($this->first === null) {
      $this->first =& $frg;
    }
    else {
      $this->last->next =& $frg;
      $frg->prev =& $this->last;
    }
    $this->last =& $frg;
  }
  
  public function insert_block_before($anchor_id) {
    $id = count($this->frgs);
    $anchor =& $this->frgs[$anchor_id];
    $frg = new Fragment($id);
    $this->frgs[$id] =& $frg;
    $frg->prev =& $anchor->prev;
    $frg->next =& $anchor;
    $frg->prev->next =& $frg;
    $anchor->prev =& $frg;
    if ($this->first === $anchor) {
      $this->first =& $frg;
    }
  }
  
  public function write($id, &$data) {
    $frg =& $this->frgs[$id];
    $this->length += $frg->write($this->tempFile,
      $this->onMemory, $this->segment, $data);
  }
  
  public function close_block($id) {
    // NOP
  }
  
  public function serial_write(&$data) {
    _cti_output($this->out, $data);
  }
  
  public function finish() {
    if ($this->out === null) {
      header("Content-Length: ".$this->length);
    }
    $frg =& $this->first;
    while ($frg !== null) {
      $frg->flush($this->tempFile, $this->out);
      $frg =& $frg->next;
    }
    fclose($this->tempFile);
  }
  
  public function dispose() {
    // NOP
  }
}
