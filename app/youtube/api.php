<?php
declare(strict_types=1);
http_response_code(410);
header('Content-Type: application/json; charset=UTF-8');
header('Cache-Control: no-store');
echo json_encode([
    'error' => 'YouTubeアップロードは移転しました。新しい画面で管理者ログインしてください。',
    'upload_url' => 'https://tatsuhiko.miya.be/youtube/',
], JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES);
