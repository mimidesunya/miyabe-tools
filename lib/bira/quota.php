<?php
declare(strict_types=1);

// AI呼び出しの回数制限。アカウントごとに1日30回。
//
// 回数だけでは費用が止まらない（1回に貼れる量が効く）ので、貼り付け量の
// 上限は入力側の検証で別に掛けている。ここは回数だけを見る。
// 表面の差し替えはAIを使わないので、この数を消費しない。

require_once dirname(__DIR__) . DIRECTORY_SEPARATOR . 'municipalities.php';

const BIRA_DAILY_LIMIT = 30;

function bira_quota_db_path(): string
{
    return data_path('bira/usage.sqlite');
}

function bira_quota_today(): string
{
    // 日本時間の0時で戻す。画面にもそう書く。
    return app_now_tokyo('Y-m-d');
}

function bira_quota_pdo(): PDO
{
    $path = bira_quota_db_path();
    $dir = dirname($path);
    if (!is_dir($dir) && !@mkdir($dir, 0775, true) && !is_dir($dir)) {
        throw new RuntimeException('利用回数の記録先を作成できませんでした。');
    }
    $pdo = new PDO('sqlite:' . $path, null, null, [
        PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION,
        PDO::ATTR_DEFAULT_FETCH_MODE => PDO::FETCH_ASSOC,
    ]);
    $pdo->exec('CREATE TABLE IF NOT EXISTS usage (
        line_user_id TEXT NOT NULL,
        day TEXT NOT NULL,
        count INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (line_user_id, day)
    )');
    return $pdo;
}

/** きょう何回使ったか。記録先が壊れていても画面は出したいので0を返す。 */
function bira_quota_used(string $lineUserId): int
{
    if ($lineUserId === '') {
        return 0;
    }
    try {
        $statement = bira_quota_pdo()->prepare('SELECT count FROM usage WHERE line_user_id = ? AND day = ?');
        $statement->execute([$lineUserId, bira_quota_today()]);
        $row = $statement->fetch();
        return $row ? max(0, (int)$row['count']) : 0;
    } catch (Throwable $error) {
        error_log('[bira] quota read failed: ' . $error->getMessage());
        return 0;
    }
}

function bira_quota_remaining(string $lineUserId): int
{
    return max(0, BIRA_DAILY_LIMIT - bira_quota_used($lineUserId));
}

/**
 * 1回ぶん使う。上限に達していれば false を返し、数えない。
 * 生成の前に呼ぶ。失敗した生成も数えるが、そうしないと失敗を繰り返して
 * 上限を回避できてしまう。
 */
function bira_quota_consume(string $lineUserId): bool
{
    if ($lineUserId === '') {
        return false;
    }
    try {
        $pdo = bira_quota_pdo();
        $pdo->beginTransaction();
        $day = bira_quota_today();
        $statement = $pdo->prepare('SELECT count FROM usage WHERE line_user_id = ? AND day = ?');
        $statement->execute([$lineUserId, $day]);
        $row = $statement->fetch();
        $used = $row ? max(0, (int)$row['count']) : 0;
        if ($used >= BIRA_DAILY_LIMIT) {
            $pdo->rollBack();
            return false;
        }
        $update = $pdo->prepare(
            'INSERT INTO usage (line_user_id, day, count) VALUES (?, ?, 1)
             ON CONFLICT(line_user_id, day) DO UPDATE SET count = count + 1'
        );
        $update->execute([$lineUserId, $day]);
        $pdo->commit();
        return true;
    } catch (Throwable $error) {
        error_log('[bira] quota consume failed: ' . $error->getMessage());
        // 数えられないときは通す。記録の不調で利用者を止めるほうが損。
        return true;
    }
}
