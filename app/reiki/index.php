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
    <?php echo site_render_favicon_links(); ?>
    <?php $cssVer = @filemtime(__DIR__ . '/assets/css/reiki.css') ?: 1; ?>
    <?php $jsVer  = @filemtime(__DIR__ . '/assets/js/reiki.js')  ?: 1; ?>
    <link rel="stylesheet" href="/reiki/assets/css/reiki.css?v=<?php echo $cssVer; ?>">
</head>
<body data-reiki-slug="<?php echo h($requestSlug); ?>">
<header class="header">
    <div style="display:flex; align-items:center; gap:10px;">
        <h1><a href="<?php echo h($clearUrl); ?>" style="color:inherit; text-decoration:none;"><?php echo h($pageTitle); ?></a></h1>
        <button id="menu-toggle" type="button">🔍 検索・一覧</button>
    </div>
    <div style="display:flex; align-items:center; gap:10px; flex-wrap:wrap; justify-content:flex-end;">
        <select aria-label="自治体切り替え" onchange="if (this.value) { window.location.href = this.value; }" style="padding:8px 10px; border:1px solid #cbd5e1; border-radius:999px; font-size:13px;">
            <?php foreach ($switcherItems as $item): ?>
                <?php $switchMunicipality = municipality_entry((string)$item['slug']); ?>
                <?php $switchUrl = (string)($switchMunicipality['reiki']['url'] ?? ''); ?>
                <option value="<?php echo h($switchUrl); ?>" <?php echo $item['slug'] === $slug ? 'selected' : ''; ?>>
                    <?php echo h($item['name'] . (!empty($item['enabled']) ? '' : ' (準備中)')); ?>
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
        <form class="search" method="get">
            <input type="hidden" name="slug" value="<?php echo h($requestSlug); ?>">
            <input type="text" name="q" value="<?php echo h($q); ?>" placeholder="タイトル・ファイル名で検索">
            <?php if ($selectedRecord): ?>
                <input type="hidden" name="file" value="<?php echo h($selectedRecord['name']); ?>">
            <?php endif; ?>
            
            <div style="margin-top:8px;">
                <div style="display:flex; gap:4px; margin-bottom:4px;">
                    <select name="sort" style="flex:1; font-size:12px; padding:4px;" onchange="this.form.submit()">
                        <option value="date" <?php if ($sort==='date') echo 'selected'; ?>>制定日</option>
                        <option value="kana" <?php if ($sort==='kana') echo 'selected'; ?>>五十音順</option>
                        <option value="score_necessity" <?php if ($sort==='score_necessity') echo 'selected'; ?>>必要度スコア</option>
                        <option value="score_fiscal" <?php if ($sort==='score_fiscal') echo 'selected'; ?>>財政影響スコア</option>
                        <option value="score_burden" <?php if ($sort==='score_burden') echo 'selected'; ?>>規制負担スコア</option>
                        <option value="score_effectiveness" <?php if ($sort==='score_effectiveness') echo 'selected'; ?>>政策効果スコア</option>
                    </select>
                    <select name="dir" style="width:70px; font-size:12px; padding:4px;" onchange="this.form.submit()">
                        <option value="desc" <?php if ($direction==='desc') echo 'selected'; ?>>降順</option>
                        <option value="asc" <?php if ($direction==='asc') echo 'selected'; ?>>昇順</option>
                    </select>
                </div>
            </div>

            <div class="filter-block" data-filter-group>
                <div class="filter-title">
                    <span>判定結果 (複数選択可)</span>
                    <span class="filter-count" data-selected-count>0件選択</span>
                </div>
                <div class="checkbox-grid" data-filter-options>
                    <?php foreach ($allStances as $st): ?>
                        <label class="checkbox-item" data-filter-option>
                            <input type="checkbox" name="stance[]" value="<?php echo h($st); ?>" 
                                <?php if (!$hasStanceFilterParam || in_array($st, $filterStances, true)) echo 'checked'; ?>>
                            <span><?php echo h(get_stance_label((string)$st)); ?></span>
                        </label>
                    <?php endforeach; ?>
                </div>
            </div>
            
            <div class="filter-block" data-filter-group>
                <div class="filter-title">
                    <span>分類 (複数選択可)</span>
                    <span class="filter-count" data-selected-count>0件選択</span>
                </div>
                <div class="checkbox-grid" data-filter-options>
                    <?php foreach ($allClasses as $cls): ?>
                        <label class="checkbox-item" data-filter-option>
                            <input type="checkbox" name="class[]" value="<?php echo h($cls); ?>" 
                                <?php if (!$hasClassFilterParam || in_array($cls, $filterClasses, true)) echo 'checked'; ?>>
                            <span><?php echo h($cls); ?></span>
                        </label>
                    <?php endforeach; ?>
                </div>
            </div>

            <div class="filter-block" data-filter-group>
                <div class="filter-title">
                    <span>種別 (複数選択可)</span>
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

            <button type="submit">検索・並べ替え</button>
            
            <div style="margin-top:12px; border-top:1px solid #eef2f7; padding-top:8px;">
                <div style="font-size:11px; color:#64748b; margin-bottom:4px;">クイックフィルタ (最悪順)</div>
                <div style="display:flex; flex-wrap:wrap; gap:4px;">
                    <a href="<?php echo h(query_with(['sort' => 'score_necessity', 'dir' => 'asc', 'page' => null])); ?>" style="font-size:11px; padding:3px 8px; border:1px solid #cbd5e1; border-radius:12px; text-decoration:none; color:#334155; background:#fff;">必要度ワースト</a>
                    <a href="<?php echo h(query_with(['sort' => 'score_effectiveness', 'dir' => 'asc', 'page' => null])); ?>" style="font-size:11px; padding:3px 8px; border:1px solid #cbd5e1; border-radius:12px; text-decoration:none; color:#334155; background:#fff;">効果ワースト</a>
                    <a href="<?php echo h(query_with(['sort' => 'score_burden', 'dir' => 'desc', 'page' => null])); ?>" style="font-size:11px; padding:3px 8px; border:1px solid #cbd5e1; border-radius:12px; text-decoration:none; color:#334155; background:#fff;">規制負担ワースト</a>
                </div>
            </div>

            <?php if ($q !== '' || !empty($filterClasses) || !empty($filterStances) || !empty($filterDocTypes)): ?>
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
                        
                        <div style="display:flex; flex-wrap:wrap; gap:4px; margin-top:6px; align-items:center;">
                            <?php if (!empty($record['primary_class'])): ?>
                                <?php $primaryClassLabel = preg_replace('/^([A-G])_/', '$1 ', (string)($record['primary_class'] ?? '')); ?>
                                <span class="badge" style="font-size:10px; margin:0; padding:2px 6px; border:1px solid #cbd5e1; background:#f1f5f9; color:#475569;">
                                    <?php echo h((string)$primaryClassLabel); ?>
                                </span>
                            <?php endif; ?>
                            
                            <?php 
                            $keys = ['necessity_score', 'fiscal_impact_score', 'regulatory_burden_score', 'policy_effectiveness_score'];
                            $showAllScores = str_starts_with($sort, 'score_');
                            
                            foreach ($keys as $k):
                                if (!isset($record[$k])) continue;
                                $val = $record[$k];

                                if ($val == 0 && $k !== 'necessity_score' && !$showAllScores) {
                                    $isSortKey = ($sort === 'score_necessity' && $k === 'necessity_score') ||
                                                 ($sort === 'score_fiscal' && $k === 'fiscal_impact_score') ||
                                                 ($sort === 'score_burden' && $k === 'regulatory_burden_score') ||
                                                 ($sort === 'score_effectiveness' && $k === 'policy_effectiveness_score');
                                    if (!$isSortKey) continue;
                                }

                                list($style, $suffix) = get_score_style_and_label($k, $val);
                                $labelMap = ['necessity_score'=>'必要', 'fiscal_impact_score'=>'財政', 'regulatory_burden_score'=>'規制', 'policy_effectiveness_score'=>'効果'];
                                $label = $labelMap[$k] ?? $k;
                            ?>
                                <span style="font-size:11px; margin-right:4px; <?php echo $style; ?>">
                                    <?php echo h($label); ?>:<?php echo h((string)$val); ?> <span style="font-size:10px; opacity:0.8;"><?php echo h($suffix); ?></span>
                                </span>
                            <?php endforeach; ?>
                        </div>

                        <?php if (!empty($record['enactment_date'])): ?>
                            <div class="file-meta" style="margin-top:4px;">
                                制定日: <?php echo h($record['enactment_date']); ?>
                                <?php 
                                    $enactmentYear = (int)substr($record['enactment_date'], 0, 4);
                                    if ($enactmentYear > 1000) {
                                        $currentYear = (int)app_now_tokyo('Y');
                                        $elapsed = $currentYear - $enactmentYear;
                                        if ($elapsed > 0) {
                                            $style = $elapsed >= 50 ? 'color:#dc2626; font-weight:bold;' : '';
                                            echo "<span style='margin-left:6px; font-size:11px; {$style}'>({$elapsed}年前)</span>";
                                        }
                                    }
                                ?>
                            </div>
                        <?php endif; ?>
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
                    自治体切り替えUIには対応済みです。データを配置すると、この画面からそのまま検索・閲覧できます。
                </div>
            </div>
        <?php elseif ($selectedRecord === null): ?>
            <?php if ($isLanding): ?>
                <?php include dirname(__DIR__, 2) . DIRECTORY_SEPARATOR . 'lib' . DIRECTORY_SEPARATOR . 'reiki' . DIRECTORY_SEPARATOR . 'guide.php'; ?>
            <?php else: ?>
                <div class="empty-content">
                    <div style="font-size:40px; margin-bottom:12px;">📋</div>
                    <div style="font-size:16px; font-weight:600; color:#334155; margin-bottom:8px;">
                        <?php echo h((string)$total); ?>件の例規が見つかりました
                    </div>
                    <div style="font-size:14px; color:#64748b;">
                        左の一覧から条例を選択すると、ここに評価結果と本文が表示されます。
                    </div>
                </div>
            <?php endif; ?>
        <?php else: ?>
            <section class="card">
                <h2 class="title"><?php echo h($selectedTitle); ?></h2>

                <?php if (is_array($selectedClassification)): ?>
                    <?php if (!empty($selectedClassification['readingKana'])): ?>
                        <div class="meta" style="margin-bottom:10px; font-size:14px; color:#334155;">
                            読み: <?php echo h((string)$selectedClassification['readingKana']); ?>
                            <?php if (isset($selectedClassification['readingConfidence'])): ?>
                                （確度: <?php echo h((string)$selectedClassification['readingConfidence']); ?>）
                            <?php endif; ?>
                        </div>
                    <?php endif; ?>
                    <?php if (!empty($selectedClassification['responsibleDepartment'])): ?>
                        <div class="meta" style="margin-bottom:10px; font-size:14px; color:#334155;">
                            所管部署（推定）: <?php echo h((string)$selectedClassification['responsibleDepartment']); ?>
                            <?php if (isset($selectedClassification['departmentConfidence'])): ?>
                                （確度: <?php echo h((string)$selectedClassification['departmentConfidence']); ?>）
                            <?php endif; ?>
                        </div>
                    <?php endif; ?>
                    <?php if (!empty($selectedClassification['analyzedAt'])): ?>
                        <div class="meta" style="margin-bottom:10px; font-size:13px; color:#6b7280;">
                            AI評価日時: <?php echo h(app_format_tokyo_datetime((string)$selectedClassification['analyzedAt'])); ?>
                            <?php if (!empty($selectedClassification['modelName'])): ?>
                                (Model: <?php echo h((string)$selectedClassification['modelName']); ?>)
                            <?php endif; ?>
                        </div>
                    <?php endif; ?>
                    <div style="margin-bottom:10px;">
                        <?php if (!empty($selectedClassification['primaryClass'])): ?>
                            <span class="badge"><?php echo h((string)$selectedClassification['primaryClass']); ?></span>
                        <?php endif; ?>
                        <?php
                        $secondaryTags = $selectedClassification['secondaryTags'] ?? [];
                        if (is_array($secondaryTags)) {
                            foreach ($secondaryTags as $tag) {
                                echo '<span class="badge">' . h((string)$tag) . '</span>';
                            }
                        }
                        ?>
                    </div>
                    <dl class="kv">
                        <?php 
                        $scoreMap = [
                            'necessityScore' => ['key'=>'necessity_score', 'label'=>'必要度 (1-100)'],
                            'fiscalImpactScore' => ['key'=>'fiscal_impact_score', 'label'=>'財政負担 (1.0-5.0)'],
                            'regulatoryBurdenScore' => ['key'=>'regulatory_burden_score', 'label'=>'規制負担 (1.0-5.0)'],
                            'policyEffectivenessScore' => ['key'=>'policy_effectiveness_score', 'label'=>'政策効果 (1.0-5.0)']
                        ];
                        
                        foreach ($scoreMap as $jsonKey => $info) {
                            $val = $selectedClassification[$jsonKey] ?? null;
                            echo '<dt>' . h($info['label']) . '</dt>';
                            echo '<dd>';
                            if ($val !== null) {
                                echo get_score_html($info['key'], $val);
                            } else {
                                echo '-';
                            }
                            echo '</dd>';
                        }
                        ?>

                        <dt>判定理由</dt>
                        <dd style="line-height:1.6;"><?php echo nl2br(h((string)($selectedClassification['reason'] ?? '-'))); ?></dd>
                    </dl>
                <?php else: ?>
                    <div class="meta">分類結果ファイル（<?php echo h((string)($reikiFeature['classification_dir_rel'] ?? 'reiki/*_json')); ?>）が未作成、または該当データなし。</div>
                <?php endif; ?>
            </section>

            <!-- Feedback Section -->
            <?php $filenameStem = pathinfo($selectedRecord['name'], PATHINFO_FILENAME); ?>
            <section class="card" id="feedback-section" data-filename="<?php echo h($filenameStem); ?>" data-slug="<?php echo h($requestSlug); ?>">
                <div class="feedback-row">
                    <span style="font-size:14px; font-weight:600; color:#334155;">このAI評価はどうですか？</span>
                    <div class="feedback-buttons">
                        <button type="button" id="btn-good" onclick="submitVote('good')">
                            👍 <span id="count-good">…</span>
                        </button>
                        <button type="button" id="btn-bad" onclick="submitVote('bad')">
                            👎 <span id="count-bad">…</span>
                        </button>
                    </div>
                    <span id="vote-status"></span>
                    <span class="view-count-label">👁 <span id="view-count">…</span> 回閲覧</span>
                </div>
                <div style="margin-top:10px;">
                    <input type="text" id="comment-input" maxlength="200" placeholder="短いコメント（任意・200文字以内）">
                </div>
                <div id="comments-list" style="margin-top:12px; display:none;">
                    <div style="font-size:12px; font-weight:600; color:#475569; margin-bottom:6px;">最近のコメント</div>
                    <ul id="comments-ul" style="margin:0; padding:0; list-style:none; font-size:13px; max-height:200px; overflow-y:auto;"></ul>
                </div>
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
