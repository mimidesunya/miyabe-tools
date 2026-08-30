<?php
namespace CTI\Results;

use CTI\Builder\NullBuilder;

/**
 * 単一の結果を返すResultsの実装です。
 */
class SingleResult implements Results {
  private $builder;
  
  public function __construct($builder) {
    $this->builder = $builder;
  }

  public function next_builder($opts = null) {
    if (!isset($this->builder)) {
      return new NullBuilder();
    }
    $builder = $this->builder;
    unset($this->builder);
    return $builder;
  }
}
