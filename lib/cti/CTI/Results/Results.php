<?php
namespace CTI\Results;

/**
 * 結果インターフェースです。
 */
interface Results {
  public function next_builder($opts = null);
}
