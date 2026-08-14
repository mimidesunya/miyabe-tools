<?php
declare(strict_types=1);

require_once dirname(__DIR__) . DIRECTORY_SEPARATOR . 'lib' . DIRECTORY_SEPARATOR . 'homepage' . DIRECTORY_SEPARATOR . 'runtime.php';

function document_only_assert(bool $condition, string $message): void
{
    if (!$condition) {
        fwrite(STDERR, "FAIL: {$message}\n");
        exit(1);
    }
}

$labels = homepage_document_feature_labels();
document_only_assert(array_keys($labels) === ['gijiroku', 'reiki'], 'トップの機能定義は会議録・例規集だけ');

$payload = homepage_sanitize_api_payload_displays([
    'feature_summaries' => [
        ['feature_key' => 'gijiroku'],
        ['feature_key' => 'boards'],
    ],
    'municipalities' => [
        [
            'slug' => '14130-kawasaki-shi',
            'name' => '川崎市',
            'prefecture_code' => '14',
            'prefecture_label' => '神奈川県',
            'features' => [
                ['feature_key' => 'gijiroku', 'label' => '会議録', 'mode' => 'link'],
                ['feature_key' => 'boards', 'label' => '選挙ポスター掲示場', 'mode' => 'link'],
            ],
        ],
        [
            'slug' => 'boards-only',
            'name' => '掲示場のみ',
            'prefecture_code' => '14',
            'prefecture_label' => '神奈川県',
            'features' => [
                ['feature_key' => 'boards', 'label' => '選挙ポスター掲示場', 'mode' => 'link'],
            ],
        ],
    ],
]);

document_only_assert(count($payload['municipalities'] ?? []) === 1, '掲示場だけの自治体をトップAPIから除外する');
document_only_assert(
    array_column($payload['municipalities'][0]['features'] ?? [], 'feature_key') === ['gijiroku'],
    '自治体カードから掲示場機能を除外する'
);
document_only_assert(
    array_column($payload['feature_summaries'] ?? [], 'feature_key') === ['gijiroku'],
    '機能集計から掲示場を除外する'
);

$indexSource = file_get_contents(dirname(__DIR__) . DIRECTORY_SEPARATOR . 'app' . DIRECTORY_SEPARATOR . 'index.php');
$javascriptSource = file_get_contents(dirname(__DIR__) . DIRECTORY_SEPARATOR . 'app' . DIRECTORY_SEPARATOR . 'assets' . DIRECTORY_SEPARATOR . 'js' . DIRECTORY_SEPARATOR . 'home.js');
document_only_assert(is_string($indexSource) && is_string($javascriptSource), 'トップ画面のソースを読み込める');
document_only_assert(!str_contains($indexSource, 'data-feature-filter="boards"'), '掲示場フィルターを表示しない');
document_only_assert(str_contains($indexSource, 'href="/boards/"'), '独立した掲示場ページへのリンクは維持する');
document_only_assert(!str_contains($javascriptSource, "renderFeatureDot('boards'"), '自治体一覧に掲示場ドットを描画しない');
document_only_assert(!str_contains($javascriptSource, "shortLabel: '掲'"), '自治体一覧用の「掲」ラベルを持たない');

fwrite(STDOUT, "OK: homepage municipality catalog is document-only\n");
