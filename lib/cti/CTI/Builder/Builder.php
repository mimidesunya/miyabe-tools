<?php
namespace CTI\Builder;

/**
 * ビルダーインターフェースです。
 */
interface Builder {
  public function add_block();
  public function insert_block_before($anchor_id);
  public function write($id, &$data);
  public function close_block($id);
  public function serial_write(&$data);
  public function finish();
  public function dispose();
}
