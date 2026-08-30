<?php
namespace CTI\Builder;

/**
 * ファイルに出力するビルダーです。
 */
class FileBuilder extends StreamBuilder {
  protected $file;
  
  public function __construct($file) {
    parent::__construct();
    $this->file = $file;
  }

  public function serial_write(&$data) {
    if (!isset($this->out)) {
      $this->out = fopen($this->file, 'w');
    }
    _cti_output($this->out, $data);
  }
  
  public function finish() {
    if (!isset($this->out)) {
      $this->out = fopen($this->file, 'w');
      parent::finish();
    }
    fclose($this->out);
    unset($this->out);
  }
}
