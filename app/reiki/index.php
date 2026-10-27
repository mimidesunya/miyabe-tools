<?php
declare(strict_types=1);

require_once dirname(__DIR__, 2) . DIRECTORY_SEPARATOR . 'lib' . DIRECTORY_SEPARATOR . 'reiki' . DIRECTORY_SEPARATOR . 'index_runtime.php';
require_once dirname(__DIR__, 2) . DIRECTORY_SEPARATOR . 'lib' . DIRECTORY_SEPARATOR . 'site_assets.php';

// 例規集ページの画面骨格は app 側に置き、前処理だけ lib 側へ分ける。
?><!doctype html>
<html lang="ja">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title><?php echo h($pageTitle); ?></title>
    <?php
    // 検索エンジンに載る画面なので、説明と canonical を出す。
    $metaDescription = sprintf(
        '%sの例規集（条例・規則）を全文検索できます。分野別の目次から条文をたどれます。',
        (string)($municipality['name'] ?? '')
    );
    echo site_render_page_meta($pageTitle, $metaDescription, '/reiki/?slug=' . rawurlencode($requestSlug));
    ?>
    <?php echo site_render_favicon_links(); ?>
    <?php $cssVer = @filemtime(__DIR__ . '/assets/css/reiki.css') ?: 1; ?>
    <?php $jsVer  = @filemtime(__DIR__ . '/assets/js/reiki.js')  ?: 1; ?>
    <link rel="stylesheet" href="/reiki/assets/css/reiki.css?v=<?php echo $cssVer; ?>">
</head>
<body data-reiki-slug="<?php echo h($requestSlug); ?>">
<header class="header">
    <div style="display:flex; align-items:center; gap:10px;">
        <h1><a href="<?php echo h($clearUrl); ?>" style="color:inherit; text-decoration:none;"><?php echo h($pageTitle); ?></a></h1>
        <button id="menu-toggle" type="button">検索・一覧</button>
    </div>
    <div style="display:flex; align-items:center; gap:10px; flex-wrap:wrap; justify-content:flex-end;">
        <select aria-label="自治体切り替え" onchange="if (this.value) { window.location.href = this.value; }" style="padding:8px 10px; border:1px solid #cbd5e1; border-radius:999px; font-size:13px;">
            <?php foreach ($switcherItems as $item): ?>
                <?php $switchMunicipality = municipality_entry((string)$item['slug']); ?>
                <?php $switchUrl = (string)($switchMunicipality['reiki']['url'] ?? ''); ?>
                <option value="<?php echo h($switchUrl); ?>" <?php echo $item['slug'] === $slug ? 'selected' : ''; ?>>
                    <?php echo h($item['name'] . (!empty($item['enabled']) ? '' : '（準備中）')); ?>
                </option>
            <?php endforeach; ?>
        </select>
        <div class="meta"><?php echo $featureAvailable ? '全' . h((string)$total) . '本' : '準備中'; ?></div>
    </div>
</header>
<?php
$layoutClasses = 'layout';
if ($selectedRecord !== null) $layoutClasses .= ' has-detail';
elseif ($isLanding) $layoutClasses .= ' is-landing';
?>
<div class="<?php echo $layoutClasses; ?>">
    <nav class="mobile-back">
        <a href="<?php echo h(query_with(['file' => null])); ?>">← 一覧に戻る</a>
    </nav>
    <aside class="sidebar">
        <form class="search" method="get" action="/reiki/">
            <input type="hidden" name="slug" value="<?php echo h($requestSlug); ?>">
            <input type="text" name="q" value="<?php echo h($q); ?>" placeholder="タイトルで検索">
            
            <div class="filter-block" data-filter-group>
                <div class="filter-title">
                    <span>種別（複数選べます）</span>
                    <span class="filter-count" data-selected-count>0件選択</span>
                </div>
                <div class="checkbox-grid" data-filter-options>
                    <?php foreach ($documentTypeOptions as $docType): ?>
                        <label class="checkbox-item" data-filter-option>
                            <input type="checkbox" name="doctype[]" value="<?php echo h($docType); ?>"
                                <?php if (!$hasDocTypeFilterParam || in_array($docType, $filterDocTypes, true)) echo 'checked'; ?>>
                            <span><?php echo h($docType); ?></span>
                        </label>
                    <?php endforeach; ?>
                </div>
            </div>

            <button type="submit">検索</button>
            
            <?php if ($q !== '' || !empty($filterDocTypes)): ?>
                <div style="margin-top:8px; font-size:12px; color:#64748b;">
                    検索結果: <?php echo h((string)$total); ?>件
                    <br>
                    <a href="<?php echo h($clearUrl); ?>" style="color:#275ea3; text-decoration:none;">[×] 条件をクリア</a>
                </div>
            <?php endif; ?>
        </form>

        <ul class="list">
            <?php foreach ($pagedRecords as $record): ?>
                <?php
                $active = $selectedRecord && $selectedRecord['name'] === $record['name'];
                $class = $active ? 'active' : '';
                $link = query_with(['file' => $record['name'], 'page' => null]);
                ?>
                <li>
                    <a class="<?php echo h($class); ?>" href="<?php echo h($link); ?>">
                        <div class="law-title"><?php echo h($record['title'] !== '' ? $record['title'] : ($titleCache[$record['name']] ?? $record['name'])); ?></div>
                    </a>
                </li>
            <?php endforeach; ?>
            <?php if (empty($pagedRecords)): ?>
                <li><div style="padding:12px; color:#64748b;">該当ファイルがありません。</div></li>
            <?php endif; ?>
        </ul>
        
        <div class="pager">
            <?php if ($page > 1): ?>
                <a href="<?php echo h(query_with(['page' => $page - 1])); ?>">前へ</a>
            <?php endif; ?>
            <span><?php echo h((string)$page); ?> / <?php echo h((string)$totalPages); ?></span>
            <?php if ($page < $totalPages): ?>
                <a href="<?php echo h(query_with(['page' => $page + 1])); ?>">次へ</a>
            <?php endif; ?>
        </div>
    </aside>

    <main class="content">
        <?php if (!$featureAvailable): ?>
            <div class="empty-content">
                <div style="font-size:40px; margin-bottom:12px;">🏗</div>
                <div style="font-size:16px; font-weight:600; color:#334155; margin-bottom:8px;">
                    <?php echo h($featureNotice); ?>
                </div>
                <div style="font-size:14px; color:#64748b;">
                    自治体の切り替えには対応しています。データを置けば、この画面から検索して読めます。
                </div>
            </div>
        <?php elseif ($selectedRecord === null): ?>
            <div class="empty-content">
                <div style="font-size:40px; margin-bottom:12px;">📋</div>
                <div style="font-size:16px; font-weight:600; color:#334155; margin-bottom:8px;">
                    <?php echo h((string)$total); ?>件の例規<?php echo $isLanding ? 'があります' : 'が見つかりました'; ?>
                </div>
                <div style="font-size:14px; color:#64748b;">
                    左の一覧で例規を選ぶと、ここに本文が出ます。
                </div>
            </div>
        <?php else: ?>
            <section class="card">
                <h2 class="title"><?php echo h($selectedTitle); ?></h2>
            </section>

            <section class="card">
                <div class="law-content">
                    <?php if ($selectedContentHtml !== ''): ?>
                        <?php echo $selectedContentHtml; ?>
                    <?php else: ?>
                        <pre><?php echo h($selectedText); ?></pre>
                    <?php endif; ?>
                </div>
            </section>
        <?php endif; ?>
    </main>
</div>
<script src="/reiki/assets/js/reiki.js?v=<?php echo $jsVer; ?>"></script>
</body>
</html>
