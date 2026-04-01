<?php
declare(strict_types=1);

require_once dirname(__DIR__, 2) . DIRECTORY_SEPARATOR . 'lib' . DIRECTORY_SEPARATOR . 'session.php';

function h(?string $value): string
{
    return htmlspecialchars($value ?? '', ENT_QUOTES | ENT_SUBSTITUTE, 'UTF-8');
}

$requestedSlug = $_GET['slug'] ?? null;
redirect_to_canonical_boards_slug_if_needed(is_string($requestedSlug) ? $requestedSlug : null);
$slug = get_slug();
$municipality = municipality_entry($slug);
if ($municipality === null) {
    http_response_code(404);
    echo '自治体が見つかりません。';
    exit;
}

$switcherItems = municipality_switcher_items('boards');
$pageTitle = (string)($municipality['boards']['title'] ?? ($municipality['name'] . ' ポスター掲示場'));
?><!DOCTYPE html>
<html lang="ja">

<head>
  <meta charset="UTF-8">
  <title><?php echo h($pageTitle); ?></title>
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" />
  <?php
  $assetDir = __DIR__ . '/assets';
  $assetFiles = array_merge(
      glob($assetDir . '/css/*.css') ?: [],
      glob($assetDir . '/js/*.js')   ?: []
  );
  $assetVer = max(array_map('filemtime', $assetFiles));
  ?>
  <link rel="stylesheet" href="/boards/assets/css/style.css?v=<?php echo $assetVer; ?>" />
  <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
  <style>
    #page-header {
      position: fixed;
      top: 8px;
      left: 8px;
      z-index: 1300;
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      gap: 6px;
      max-width: min(680px, calc(100vw - 16px));
      padding: 7px 10px;
      border-radius: 10px;
      border: 1px solid rgba(15, 23, 42, 0.12);
      background: rgba(255, 255, 255, 0.94);
      box-shadow: 0 4px 14px rgba(15, 23, 42, 0.12);
      backdrop-filter: blur(10px);
    }
    .page-links {
      display: flex;
      flex-wrap: nowrap;
      gap: 5px;
      margin-left: auto;
      align-items: center;
    }
    .page-links a {
      font-size: 12px;
      border-radius: 999px;
      border: 1px solid #d4dbe3;
      background: #fff;
      color: #1f2937;
      text-decoration: none;
      padding: 5px 10px;
      white-space: nowrap;
    }
    .page-links a.kml-link {
      font-size: 10px;
      padding: 4px 7px;
      color: #999;
      border-color: #e8eaed;
    }
    .page-links select {
      font-size: 12px;
      border-radius: 999px;
      border: 1px solid #d4dbe3;
      background: #fff;
      color: #1f2937;
      padding: 5px 8px;
      max-width: 160px;
    }
    .gps-hint {
      display: block;
      font-size: 10px;
      font-weight: normal;
      opacity: 0.65;
      line-height: 1.2;
    }
    #gps-btn.active .gps-hint { display: none; }
    @media (max-width: 480px) {
      #page-header {
        right: 8px;
      }
      .page-links { flex-wrap: wrap; width: 100%; margin-left: 0; }
      .page-links select { max-width: 100%; flex: 1 1 auto; }
    }
  </style>
</head>

<body>
  <script>if(/Line\//i.test(navigator.userAgent)&&!location.search.includes('openExternalBrowser=1')){var u=new URL(location.href);u.searchParams.set('openExternalBrowser','1');location.replace(u.toString());}</script>
  <div id="page-header">
    <div class="page-links">
      <a href="<?php echo h((string)$municipality['boards']['list_url']); ?>">一覧</a>
      <a href="/boards/api/kml.php?slug=<?php echo h($slug); ?>" download class="kml-link">KML</a>
      <select aria-label="自治体切り替え" onchange="if (this.value) { window.location.href = this.value; }">
        <?php foreach ($switcherItems as $item): ?>
          <?php if (!$item['enabled'] && $item['slug'] !== $slug) continue; ?>
          <option value="<?php echo h($item['url']); ?>" <?php echo $item['slug'] === $slug ? 'selected' : ''; ?>>
            <?php echo h($item['name']); ?>
          </option>
        <?php endforeach; ?>
      </select>
    </div>
  </div>
  <div id="controls">
    <div id="auth" style="display:flex; gap:8px; align-items:center; justify-content:flex-end;">
      <span id="auth-name" style="display:none;"></span>
      <a id="auth-login" href="/line/login.php?slug=<?php echo h($slug); ?>" style="display:none;">LINEでログインして編集</a>
      <a id="auth-logout" href="/line/logout.php" style="display:none;">ログアウト</a>
    </div>
    <div style="display:flex; gap:6px; align-items:center;">
      <input id="search-input" type="text" placeholder="code / 住所 / 設置場所 で検索" />
      <button id="search-btn">検索</button>
      <button id="help-btn">ヘルプ</button>
    </div>
    <div style="display:flex; gap:6px; align-items:center; justify-content:flex-end;">
      <button id="gps-btn"><span id="gps-label">GPS: OFF</span><span class="gps-hint">現在地を追従させるにはこちら</span></button>
    </div>

  </div>
  <div id="map"></div>
  <div id="legend-wrap">
    <div id="legend">
      <div>
        <span class="legend-swatch status-in_progress">⏳ 着手</span>
        <span class="legend-count" id="legend-count-in_progress">—</span>
      </div>
      <div>
        <span class="legend-swatch status-done">✅ 掲示</span>
        <span class="legend-count" id="legend-count-done">—</span>
      </div>
      <div>
        <span class="legend-swatch status-issue">⚠️ 異常</span>
        <span class="legend-count" id="legend-count-issue">—</span>
      </div>
      <hr style="border:none;border-top:1px solid #eee;margin:8px 0;">
      <div style="font-size:13px; color:#333; line-height:1.3;">
        <div>👤: あなたが更新</div>
        <div>💬: コメントあり</div>
      </div>
    </div>
    <button id="offset-toggle" style="display:none;">位置調整: OFF</button>
  </div>
  <div id="help-modal"
    style="display:none; position:fixed; inset:0; background: rgba(0,0,0,0.4); z-index:2000; align-items:center; justify-content:center;">
    <div
      style="background:#fff; max-width: 92vw; width: 600px; max-height: 86vh; overflow:auto; border-radius:10px; box-shadow:0 6px 24px rgba(0,0,0,0.25);">
      <div
        style="padding:14px 16px; border-bottom:1px solid #eee; display:flex; justify-content:space-between; align-items:center;">
        <div style="font-weight:700;">使い方ガイド</div>
        <button id="help-close"
          style="border:none; background:#f3f4f6; border-radius:6px; padding:6px 10px; cursor:pointer;">閉じる</button>
      </div>
      <div style="padding:14px 16px; font-size:14px; color:#333; line-height:1.7;">
        <p style="margin-bottom: 1em;">このマップは、選挙ポスター掲示場の設置・撤去状況をリアルタイムで共有するためのツールです。</p>

        <h3 style="font-size:15px; font-weight:700; margin:1em 0 0.5em; border-left:4px solid #34a853; padding-left:8px;">基本操作</h3>
        <ul style="padding-left: 20px; margin: 0;">
          <li><b>検索:</b> 掲示場番号 (code)、住所、設置場所名で検索できます。</li>
          <li><b>GPS:</b> 画面右上の「GPS: OFF」ボタンを押すと、現在地を追跡してマップを自動移動します。</li>
          <li><b>詳細表示:</b> マーカー（番号）をタップすると、詳細情報やGoogleマップへのリンクが表示されます。</li>
        </ul>

        <h3 style="font-size:15px; font-weight:700; margin:1em 0 0.5em; border-left:4px solid #34a853; padding-left:8px;">ステータスの更新（ログイン必須）</h3>
        <ul style="padding-left: 20px; margin: 0;">
          <li>ログイン後、マーカーをタップしてステータスを変更できます。</li>
          <li><b>未着手:</b> まだ作業を行っていない状態。</li>
          <li><b>⏳ 着手:</b> 作業予定、または作業中の状態。</li>
          <li><b>✅ 掲示:</b> ポスター掲示（または撤去）が完了した状態。</li>
          <li><b>⚠️ 異常:</b> 掲示板が破損している、他陣営のポスターで埋まっているなど、作業できない状態。</li>
          <li>異常時はコメント欄に詳細を記入してください。</li>
        </ul>

        <h3 style="font-size:15px; font-weight:700; margin:1em 0 0.5em; border-left:4px solid #34a853; padding-left:8px;">アイコンの意味</h3>
        <ul style="padding-left: 20px; margin: 0;">
          <li>👤 <b>人型アイコン:</b> あなたが最後に更新した掲示場です。</li>
          <li>💬 <b>吹き出し:</b> コメントが登録されている掲示場です。</li>
        </ul>

      <div id="help-offset-section" style="display:none;">
        <h3 style="font-size:15px; font-weight:700; margin:1em 0 0.5em; border-left:4px solid #34a853; padding-left:8px;">位置調整（ログイン必須）</h3>
        <p style="margin:0;">
          選管による位置変更や、実際の掲示板位置が地図とずれている場合などに使用します。<br>
          画面右下の「位置調整: OFF」ボタンを押して ON にすると、マーカーをドラッグして表示位置を修正できます。<br>
          ※ 変更した位置情報はデータベースに保存され、他のユーザーにも共有されます。
        </p>
      </div>
      </div>
    </div>
  </div>
  <script>
  (function(){
    function adjust(){
      var h=document.getElementById('page-header');
      var c=document.getElementById('controls');
      if(h&&c) c.style.top=(h.getBoundingClientRect().bottom+8)+'px';
    }
    adjust();
    window.addEventListener('resize',adjust);
    if(window.ResizeObserver) new ResizeObserver(adjust).observe(document.getElementById('page-header'));
  })();
  </script>
  <script type="module" src="/boards/assets/js/main.js?v=<?php echo $assetVer; ?>"></script>
</body>

</html>
