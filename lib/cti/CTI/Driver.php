<?php
namespace CTI;

/**
 * CTIドライバです。
 */
class Driver {
  /**
   * 指定されたURIに接続し、セッションを返します。
   *
   * @param string $uri 接続先URI。
   * @param array|null $options 接続オプション。
   * @return Session
   */
  public function get_session($uri, $options = null) {
    $host = 'localhost';
    $port = 8099;
    if (preg_match_all('/^ctips\:\/\/([^\:\/]+)\:([0-9]+)\/?$/', $uri, $out)) {
      $host = 'tls://'.$out[1][0];
      $port = $out[2][0];
    }
    else if (preg_match_all('/^ctips:\/\/([^:\/]+)\/?$/', $uri, $out)) {
      $host = 'tls://'.$out[1][0];
    }
    else if (preg_match_all('/^ctip\:\/\/([^\:\/]+)\:([0-9]+)\/?$/', $uri, $out)) {
      $host = $out[1][0];
      $port = $out[2][0];
    }
    else if (preg_match_all('/^ctip:\/\/([^:\/]+)\/?$/', $uri, $out)) {
      $host = $out[1][0];
    }
    if (($fp = fsockopen($host, $port, $errno, $errmsg)) === false) {
      throw new \Exception(__FUNCTION__.": socket_connect() failed: $errno / $errmsg");
    }
    return new Session($fp, $options); 
  }
}
