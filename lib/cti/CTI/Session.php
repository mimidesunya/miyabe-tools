<?php
namespace CTI;

use CTI\Builder\StreamBuilder;
use CTI\Builder\FileBuilder;
use CTI\Results\SingleResult;
use CTI\Results\DirectoryResults;

/**
 * 文書変換を実行するためのセッションです。
 *
 * @package CTI
 */
class Session {
  private $encoding = 'UTF-8';
  private $fp;
  private $results;
  private $state = 1;
  private $messageFunc = null;
  private $progressFunc = null;
  private $resolverFunc = null;
  
  private $err = null;
  private $builder = null;
  private $mainLength = null;
  private $mainRead = null;
  
  /**
   * セッションのコンストラクタです。
   * セッションの作成は通常DriverManager.phpのcti_get_sessionで行うため、
   * ユーザーがコンストラクタを直接呼び出す必要はありません。
   *
   * @param resource $fp 入出力ストリーム(通常はソケット)
   * @param array|null $options 接続オプション
   */
  public function __construct($fp, $options = null) {
    $this->results = new SingleResult(new StreamBuilder());
    $this->fp = $fp;
    if (isset($options['encoding'])) {
      $this->encoding = $options['encoding'];
    }
    $user = $password = '';
    if (isset($options['user'])) {
      $user = $options['user'];
      unset($options['user']);
    }
    if (isset($options['password'])) {
      $password = $options['password'];
      unset($options['password']);
    }
    cti_ctip_connect($this->fp, $this->encoding);
    if (empty($options)) {
      $data = "PLAIN: $user $password\n";
    }
    else {
      $data = "OPTIONS: user=$user&password=$password";
      foreach($options as $k => $v) {
        $data .= "&$k=$v";
      }
      $data .= "\n";
    }
    \_cti_write($this->fp, $data);
    $res = \_cti_read($this->fp, 4);
    if ($res !== "OK \n") {
      throw new \Exception (__FUNCTION__.": Authentication failure.");
    }
  }

  /**
   * サーバー情報を返します。
   * 詳細は<a href="http://sourceforge.jp/projects/copper/wiki/CTIP2.0%E3%81%AE%E3%82%B5%E3%83%BC%E3%83%90%E3%83%BC%E6%83%85%E5%A0%B1">
   * オンラインのドキュメント</a>をご覧下さい。
   * 
   * @param string $uri サーバー情報のURI
   * @return string サーバー情報のデータ
   */
  public function get_server_info($uri) {
    cti_ctip_req_server_info($this->fp, $uri);
    $data = '';
    for ($next = cti_ctip_res_next($this->fp);
        $next['type'] != CTI_CTIP_RES_EOF;
        $next = cti_ctip_res_next($this->fp)) {
      $data .= $next['bytes'];
    }
    return $data;
  }
  
  /**
   * 変換結果の出力先を指定します。
   *
   * transcodeおよびstart_mainの前に呼び出してください。
   * この関数を呼び出さない場合、出力先は標準出力になります。
   * 出力先が標準出力の場合、自動的にContent-Lengthヘッダが送出されます。
   * 
   * @param Results\Results $results 出力先
   */
  public function set_results($results) {
    if ($this->state >= 2) {
      throw new \Exception (__FUNCTION__.": Main content is already sent.");
    }
    $this->results = $results;
  }
  
  /**
   * 変換結果の出力先ファイル名を指定します。
   *
   * set_resultsの簡易版です。
   * こちらは、１つだけ結果を出力するファイル名を直接設定出来ます。
   * 
   * @param string $file 出力先ファイル名。
   */
  public function set_output_as_file($file) {
    $this->set_results(new SingleResult(new FileBuilder($file)));
  }
    
  /**
   * 変換結果の出力先ディレクトリ名を指定します。
   *
   * set_resultsの簡易版です。
   * こちらは、複数の結果をファイルとして出力するディレクトリ名を直接設定出来ます。
   * ファイル名は prefix ページ番号 suffix をつなげたものです。
   * 
   * @param string $dir 出力先ディレクトリ名。
   * @param string $prefix 出力するファイルの名前の前に付ける文字列。
   * @param string $suffix 出力するファイルの名前の後に付ける文字列。
   */
  public function set_output_as_directory($dir, $prefix = '', $suffix = '') {
    $this->set_results(new DirectoryResults($dir, $prefix, $suffix));
  }
    
  /**
   * 変換結果の出力先リソースを指定します。
   *
   * set_resultsの簡易版です。
   * こちらは、１つだけ結果を出力先リソースを直接設定出来ます。
   * 
   * @param resource $fp 出力先リソース。
   */
  public function set_output_as_resource($fp) {
    $this->set_results(new SingleResult(new StreamBuilder($fp)));
  }
    
  /**
   * 変換結果の出力先文字列変数を指定します。
   *
   * set_resultsの簡易版です。
   * こちらは、１つだけ結果を出力先文字列変数を直接設定出来ます。
   * 
   * @param string $var 出力先文字列変数。
   */
  public function set_output_as_variable(&$var) {
    if (!isset($var)) {
      $var = '';
    }
    $this->set_results(new SingleResult(new StreamBuilder($var)));
  }
  
  /**
   * エラーメッセージ受信のためのコールバック関数を設定します。
   *
   * transcodeおよびstart_mainの前に呼び出してください。
   * コールバック関数の引数は、エラーコード(int)、メッセージ(string)、付属データ(array)です。
   * 
   * @param callable $messageFunc コールバック関数
   */
  public function set_message_func($messageFunc) {
    if ($this->state >= 2) {
      throw new \Exception (__FUNCTION__.": Main content is already sent.");
    }
    $this->messageFunc = $messageFunc;
  }

  /**
   * 進行状況受信のためのコールバック関数を設定します。
   *
   * transcodeおよびstart_mainの前に呼び出してください。
   * コールバック関数の引数は、全体のバイト数(int)、読み込み済みバイト数(int)です。
   * 
   * @param callable $progressFunc コールバック関数
   */
  public function set_progress_func($progressFunc) {
    if ($this->state >= 2) {
      throw new \Exception(__FUNCTION__.": Main content is already sent.");
    }
    $this->progressFunc = $progressFunc;
  }

  /**
   * リソース解決のためのコールバック関数を設定します。
   *
   * transcodeおよびstart_mainの前に呼び出してください。
   * コールバック関数の引数は、全体のバイト数(string)、リソース出力クラス(ResourceOutput)です。
   * 
   * @param callable $resolverFunc コールバック関数
   */
  public function set_resolver_func($resolverFunc) {
    if ($this->state >= 2) {
      throw new \Exception(__FUNCTION__.": Main content is already sent.");
    }
    $this->resolverFunc = $resolverFunc;
    cti_ctip_req_client_resource($this->fp, $resolverFunc ? 1 : 0);
  }

  /**
   * 複数の結果を結合するモードを切り替えます。
   * モードが有効な場合、join()の呼び出しで複数の結果を結合して返します。
   *
   * transcodeおよびstart_mainの前に呼び出してください。
   * 
   * @param bool $continuous 有効にするにはTRUE
   */
  public function set_continuous($continuous) {
    if ($this->state >= 2) {
      throw new \Exception(__FUNCTION__.": Main content is already sent.");
    }
    cti_ctip_req_continuous($this->fp, $continuous ? 1 : 0);
  }
  
  /**
   * プロパティを設定します。
   *
   * セッションを作成した直後に呼び出してください。
   * 利用可能なプロパティの一覧は「開発者ガイド」を参照してください。
   * 
   * @param string $name 名前
   * @param string $value 値
   */
  public function property($name, $value) {
    if ($this->state >= 2) {
      throw new \Exception(__FUNCTION__.": Main content is already sent.");
    }
    cti_ctip_req_property($this->fp, $name, $value);
  }
  
  /**
   * サーバー側リソースを変換します。
   * 
   * @param string $uri URI
   */
  public function transcode($uri) {
    if ($this->state >= 2) {
      throw new \Exception(__FUNCTION__.": Main content is already sent.");
    }
    cti_ctip_req_server_main($this->fp, $uri);
    $this->state = 2;
    while ($this->_build_next())
      ;
  }

  /**
   * リソース送信のための出力のバッファリングを有効にします。
   *
   * start_resource,end_resourceは対となります。
   * これらの関数はtranscodeおよびstart_mainの前に呼び出してください。
   * 
   * @param string $uri 仮想URI
   * @param array $opts リソースオプション('mimeType', 'encoding', 'length'というキーでデータ型、文字コード、長さを設定することができます。)
   */
  public function start_resource($uri, $opts = array()) {
    if ($this->state >= 2) {
      throw new \Exception(__FUNCTION__.": Main content is already sent.");
    }
    $mimeType = isset($opts['mimeType']) ? $opts['mimeType'] : 'text/css';
    $encoding = isset($opts['encoding']) ? $opts['encoding'] : '';
    $length = isset($opts['length']) ? $opts['length'] : -1;
    cti_ctip_req_resource($this->fp, $uri, $mimeType, $encoding, $length);
    // HACK ob_startのchunk_size指定によりヘッダが送信されるのを防ぐため、２重にバッファしています。
    ob_start();
    ob_start(array($this, '_resource_handler'), CTI_BUFFER_SIZE);
  }

  /**
   * バッファの内容を送信し、リソース送信のためのバッファリングを終了します。
   *
   * start_resource,end_resourceは対となります。
   * これらの関数はtranscodeおよびstart_mainの前に呼び出してください。
   */
  public function end_resource() {
    if ($this->state >= 2) {
      throw new \Exception(__FUNCTION__.": start_resource() was not called.");
    }
    ob_end_clean();
    ob_end_clean();
    cti_ctip_req_eof($this->fp);
  }

  /**
   * リソースの送信のためのコールバック関数です。
   * 
   * @param string $buffer
   * @return string
   */
  public function _resource_handler($buffer) {
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
   * 本体の変換のための出力のバッファリングを有効にします。
   *
   * start_main,end_mainは対となります。
   * 
   * @param string $uri 仮想URI
   * @param array $opts リソースオプション('mimeType', 'encoding', 'length'というキーでデータ型、文字コード、長さを設定することができます。)
   */
  public function start_main($uri = '.', $opts = array()) {
    if ($this->state >= 2) {
      throw new \Exception(__FUNCTION__.": Main content is already sent.");
    }
    $mimeType = isset($opts['mimeType']) ? $opts['mimeType'] : 'text/css';
    $encoding = isset($opts['encoding']) ? $opts['encoding'] : '';
    $length = isset($opts['length']) ? $opts['length'] : -1;
    $this->state = 2;
    cti_ctip_req_start_main($this->fp, $uri, $mimeType, $encoding, $length);
    // HACK ob_startのchunk_size指定によりヘッダが送信されるのを防ぐため、２重にバッファしています。
    ob_start();
    ob_start(array($this, '_main_handler'), CTI_BUFFER_SIZE);
  }

  /**
   * 変換結果を送信し、本体の変換のためのバッファリングを終了します。
   *
   * start_main,end_mainは対となります。
   */
  public function end_main() {
    if ($this->state != 2) {
      throw new \Exception(__FUNCTION__.": start_main() was not called.");
    }
    ob_end_clean();
    ob_end_clean();
    cti_ctip_req_eof($this->fp);
    while ($this->_build_next())
      ; 
  }

  /**
   * 本体の変換のためのコールバック関数です。
   * 
   * @param string $buffer
   * @return string
   */
  public function _main_handler($buffer) {
    for (;;) {
      $buff = substr($buffer, 0, CTI_BUFFER_SIZE);
      $len = strlen($buff);
      if ($len <= 0) {
        break;
      }
      $buffer = substr($buffer, $len);
      $packet = pack('NC', $len + 1, CTI_CTIP_REQ_DATA).$buff;
      $len = strlen($packet);
      for (;;) {
        $r = array($this->fp);
        $w = array($this->fp);
        $ex = null;
        if (($status = stream_select($r, $w, $ex, 0)) === false) {
          throw new \Exception('I/O Error');
        }
        if ($len > 0 && !empty($w)) {
          stream_set_blocking($this->fp, 0);
          if (($rlen = fwrite($this->fp, $packet)) === false) {
            stream_set_blocking($this->fp, 1);
            throw new \Exception('I/O Error');
          }
          stream_set_blocking($this->fp, 1);
          $packet = substr($packet, $rlen);
          $len -= $rlen;
        }
        if (!empty($r)) {
          $this->_build_next();
        }
        if ($len <= 0) {
          break;
        }
      }
    }
    return '';
  }
  
  /**
   * 変換処理の中断を要求します。
   * 
   * @param int $mode 中断モード 0=生成済みのデータを出力して中断, 1=即時中断
   */
  public function abort($mode) {
    if ($this->state >= 3) {
      throw new \Exception(__FUNCTION__.": The session is already closed.");
    }
    cti_ctip_req_abort($this->fp, $mode);
  }
    
  /**
   * 全ての状態をリセットします。
   */
  public function reset() {
    if ($this->state >= 3) {
      throw new \Exception(__FUNCTION__.": The session is already closed.");
    }
    cti_ctip_req_reset($this->fp);
    $this->progressFunc = null;
    $this->messageFunc = null;
    $this->resolverFunc = null;
    $this->results = new SingleResult(new StreamBuilder());
    $this->state = 1;
  }
    
  /**
   * 結果を結合します。
   */
  public function join() {
    if ($this->state >= 3) {
      throw new \Exception(__FUNCTION__.": The session is already closed.");
    }
    cti_ctip_req_join($this->fp);
    $this->state = 2;
    while ($this->_build_next())
      ; 
  }
  
  /**
   * セッションを閉じます。
   *
   * この関数の呼出し後、対象となったセッションに対するいかなる操作もできません。
   */
  public function close() {
    if ($this->state >= 3) {
      throw new \Exception(__FUNCTION__.": The session is already closed.");
    }
    cti_ctip_req_close($this->fp);
    $this->state = 3;
  }

  /**
   * 次のビルドタスクを実行します。
   * 
   * @return mixed 次がある場合はtrue,終わった場合はnull,エラーの場合はfalse
   */
  private function _build_next() {
    $next = cti_ctip_res_next($this->fp);
    switch ($next['type']) {
      case CTI_CTIP_RES_START_DATA:
      if(isset($this->builder)) {
        $this->builder->finish();
        $this->builder->dispose();
      }
      $this->builder = $this->results->next_builder($next);
      break;
      
      case CTI_CTIP_RES_BLOCK_DATA:
      $this->builder->write($next['block_id'], $next['bytes']);
      break;
      
      case CTI_CTIP_RES_ADD_BLOCK:
      $this->builder->add_block();
      break;
      
      case CTI_CTIP_RES_INSERT_BLOCK:
      $this->builder->insert_block_before($next['block_id']);
      break;
      
      case CTI_CTIP_RES_CLOSE_BLOCK:
      $this->builder->close_block($next['block_id']);
      break;
      
      case CTI_CTIP_RES_DATA:
      $this->builder->serial_write($next['bytes']);
      break;
      
      case CTI_CTIP_RES_MESSAGE:
      if ($this->messageFunc !== null) {
        $func = $this->messageFunc;
        $func($next['code'], $next['message'], $next['args']);
      }
      break;
      
      case CTI_CTIP_RES_MAIN_LENGTH:
      $this->mainLength = $next['length'];
      if ($this->progressFunc !== null) {
        $func = $this->progressFunc;
        $func($this->mainLength, $this->mainRead);
      }
      break;
      
      case CTI_CTIP_RES_MAIN_READ:
      $this->mainRead = $next['length'];
      if ($this->progressFunc !== null) {
        $func = $this->progressFunc;
        $func($this->mainLength, $this->mainRead);
      }
      break;
      
      case CTI_CTIP_RES_RESOURCE_REQUEST:
      $uri = $next['uri'];
      $r = new ResourceOutput($this->fp, $uri);
      if ($this->resolverFunc !== null) {
        $func = $this->resolverFunc;
        $func($uri, $r);
        $r->end();
      }
      if ($r->missing) {
        cti_ctip_req_missing_resource($this->fp, $uri);
      }
      break;
      
      case CTI_CTIP_RES_ABORT:
      if(isset($this->builder)) {
        if ($next['mode'] == 0) {
          $this->builder->finish();
        }
        $this->builder->dispose();
        unset($this->builder);
      }
      $this->mainLength = null;
      $this->mainRead = null;
      $this->state = 1;
      return false;
      
      case CTI_CTIP_RES_EOF:
      $this->builder->finish();
      $this->builder->dispose();
      unset($this->builder);
      $this->mainLength = null;
      $this->mainRead = null;
      
      case CTI_CTIP_RES_NEXT:
      $this->state = 1;
      return false;
    }
    return true;
  }
}
