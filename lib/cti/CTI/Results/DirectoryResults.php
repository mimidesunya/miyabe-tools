<?php
namespace CTI\Results;

use CTI\Builder\FileBuilder;

/**
 * ディレクトリに複数の結果を出力するResultsの実装です。
 */
class DirectoryResults implements Results {
  private $dir;
  private $prefix;
  private $suffix;
  private $counter;
    
  public function __construct($dir, $prefix = '', $suffix = '') {
    $this->dir = $dir;
    $this->prefix = $prefix;
    $this->suffix = $suffix;
    $this->counter = 0;
  }

  public function next_builder($opts = null) {
    $this->counter++;
    $dir = $this->dir;
    $prefix = $this->prefix;
    $counter = $this->counter;
    $suffix = $this->suffix;
    $builder = new FileBuilder("$dir/$prefix".$counter.$suffix);
    return $builder;
  }
}
