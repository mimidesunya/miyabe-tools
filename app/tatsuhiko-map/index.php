<?php
declare(strict_types=1);

require_once __DIR__ . DIRECTORY_SEPARATOR . 'lib.php';
?><!doctype html>
<html lang="ja">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>宮部たつひこマップ｜現在の活動場所</title>
    <?php echo site_render_page_meta(
        '宮部たつひこマップ｜現在の活動場所',
        '川崎市で活動する宮部たつひこの現在地を地図で確認できます。位置情報は本人が提供を有効にしている間だけ表示されます。',
        '/tatsuhiko-map/'
    ); ?>
    <?php echo site_render_favicon_links(); ?>
    <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
    <link rel="stylesheet" href="<?php echo tmap_h(site_asset_url('tatsuhiko-map/assets/style.css')); ?>">
</head>
<body>
<div class="tmap-shell">
    <header class="tmap-header">
        <?php echo site_render_brand('/'); ?>
        <nav aria-label="関連ページ">
            <a href="/">自治体マップ</a>
            <a href="/search/">記録を検索</a>
        </nav>
    </header>

    <main>
        <section class="tmap-lead">
            <h1>宮部たつひこマップ</h1>
            <p>
                川崎市で活動する宮部たつひこの現在の活動場所です。
                位置情報は本人が提供を有効にしている間だけ、GPS から更新されます。
            </p>
            <div class="tmap-status" data-tmap-status aria-live="polite">状態を確認しています。</div>
        </section>

        <section class="tmap-map-section" aria-label="現在地の地図">
            <div id="tmap-map" class="tmap-map" role="region" aria-label="宮部たつひこの現在地マップ"></div>
        </section>

        <footer class="tmap-footer">
            <nav aria-label="サイト内ページ">
                <a href="/">自治体マップ</a>
                <a href="/privacy/">プライバシー</a>
                <a href="/terms/">利用規約</a>
                <a href="/support/">サポート</a>
                <a href="/tatsuhiko-map/admin.php">管理</a>
            </nav>
            <span>位置情報は宮部たつひこ本人の操作で公開・非公開が切り替わります。</span>
        </footer>
    </main>
</div>

<script>
window.TMAP_API_URL = '/tatsuhiko-map/api.php';
</script>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js" defer></script>
<script src="<?php echo tmap_h(site_asset_url('tatsuhiko-map/assets/public.js')); ?>" defer></script>
</body>
</html>
