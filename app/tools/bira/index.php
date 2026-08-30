<?php
declare(strict_types=1);

// 政治活動ビラの作成画面。LINEログインと許可リストが要る。
//
// 利用者はHTMLを書けない前提。書くのは文章だけで、ボタンは1つにする。
// 表面は全国共通なので差し替えるだけ、裏面は入力した文章からAIが組む。

require_once dirname(__DIR__, 3) . DIRECTORY_SEPARATOR . 'lib' . DIRECTORY_SEPARATOR . 'session.php';
require_once dirname(__DIR__, 3) . DIRECTORY_SEPARATOR . 'lib' . DIRECTORY_SEPARATOR . 'bira' . DIRECTORY_SEPARATOR . 'back.php';
require_once dirname(__DIR__, 3) . DIRECTORY_SEPARATOR . 'lib' . DIRECTORY_SEPARATOR . 'bira' . DIRECTORY_SEPARATOR . 'quota.php';
require_once dirname(__DIR__, 3) . DIRECTORY_SEPARATOR . 'lib' . DIRECTORY_SEPARATOR . 'site_assets.php';

$me = current_user();
if (!$me) {
    require_login();
    exit;
}
// ログインできる人と、このページを使える人は別。存在も伏せるため404を返す。
if (!bira_is_allowed($me)) {
    http_response_code(404);
    header('Content-Type: text/plain; charset=UTF-8');
    echo 'Not Found';
    exit;
}

function bira_h(?string $value): string
{
    return htmlspecialchars($value ?? '', ENT_QUOTES | ENT_SUBSTITUTE, 'UTF-8');
}

// 入力はセッションに残す。作り直しのたびに打ち直させない。
$saved = is_array($_SESSION['bira_input'] ?? null) ? $_SESSION['bira_input'] : [];
// 初期値は川崎版。表側の4項目も含めて back.php が持っている。
$defaults = array_merge(bira_back_defaults(), $saved);

$errors = is_array($_SESSION['bira_errors'] ?? null) ? $_SESSION['bira_errors'] : [];
$failure = trim((string)($_SESSION['bira_failure'] ?? ''));
$notice = trim((string)($_SESSION['bira_notice'] ?? ''));
unset($_SESSION['bira_errors'], $_SESSION['bira_failure'], $_SESSION['bira_notice']);

if (empty($_SESSION['bira_token'])) {
    $_SESSION['bira_token'] = bin2hex(random_bytes(16));
}
$token = (string)$_SESSION['bira_token'];

$remaining = bira_quota_remaining((string)($me['id'] ?? ''));

?><!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta name="robots" content="noindex, nofollow">
<title>ビラをつくる</title>
<?php echo site_render_favicon_links(); ?>
<style>
:root { --bg:#f6f8fb; --card:#fff; --ink:#222; --muted:#667788; --accent:#275ea3; --bad:#b3261e; --ok:#1b7f4b; }
* { box-sizing: border-box; }
body { margin:0; background:var(--bg); color:var(--ink); line-height:1.75;
       font-family:-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Noto Sans JP', 'Hiragino Kaku Gothic ProN', Meiryo, sans-serif; }
header { display:flex; align-items:center; justify-content:space-between; gap:12px; flex-wrap:wrap;
         padding:12px 16px; background:#fff; box-shadow:0 1px 4px rgba(0,0,0,.06); }
header .title { font-weight:700; }
header a { color:var(--accent); text-decoration:none; font-size:14px; }
.container { max-width:720px; margin:20px auto 70px; padding:0 14px; }
.card { background:var(--card); border-radius:10px; box-shadow:0 2px 8px rgba(0,0,0,.06); padding:22px; margin-bottom:18px; }
h1 { font-size:22px; margin:0 0 8px; }
.step { display:flex; align-items:baseline; gap:10px; margin:0 0 4px; }
.step b { background:var(--accent); color:#fff; width:26px; height:26px; border-radius:50%;
          display:inline-flex; align-items:center; justify-content:center; font-size:14px; flex:0 0 auto; }
.step h2 { font-size:18px; margin:0; }
.lead { color:var(--muted); font-size:14px; margin:2px 0 0 36px; }
.fields { margin-left:36px; }
.field { margin:16px 0 0; }
.field label { display:block; font-weight:700; font-size:15px; margin-bottom:3px; }
.field .hint { color:var(--muted); font-size:13px; margin:0 0 6px; }
.field input, .field textarea { width:100%; padding:11px 13px; font:inherit; font-size:16px;
       border:1px solid #c8d2de; border-radius:8px; background:#fff; }
.field textarea { min-height:150px; resize:vertical; line-height:1.7; }
.field input:focus, .field textarea:focus { outline:2px solid var(--accent); outline-offset:1px; border-color:var(--accent); }
.field.is-bad input, .field.is-bad textarea { border-color:var(--bad); }
.field .bad { color:var(--bad); font-size:13px; margin:5px 0 0; font-weight:700; }
.grid2 { display:grid; grid-template-columns:1fr 1fr; gap:0 16px; }
@media (max-width:560px) { .grid2 { grid-template-columns:1fr; } .lead, .fields { margin-left:0; } }
.check { display:flex; gap:9px; align-items:flex-start; margin-top:16px; font-size:14px; }
.check input { margin-top:6px; width:18px; height:18px; flex:0 0 auto; }
.go { text-align:center; padding:26px 22px; }
.go button { font:inherit; font-weight:700; font-size:19px; padding:18px 44px; border-radius:10px;
             border:0; background:var(--accent); color:#fff; cursor:pointer; }
.go button[disabled] { opacity:.45; cursor:not-allowed; }
.go .sub { color:var(--muted); font-size:13px; margin:12px 0 0; }
.note { background:#fffbe6; border:1px solid #f0dfa0; border-radius:8px; padding:16px 18px; font-size:14px; }
.note h2 { font-size:15px; margin:0 0 6px; }
.note ul { margin:6px 0 0; padding-left:1.2em; }
.failure { background:#fdecea; border:1px solid #f5c2bd; color:var(--bad); font-weight:700; }
.notice { background:#eaf6ee; border:1px solid #b9dfc7; color:var(--ok); font-weight:700; }
</style>
</head>
<body>
<header>
  <span class="title">ビラをつくる</span>
  <a href="/line/profile.php">アカウント</a>
</header>

<div class="container">

<?php if ($failure !== ''): ?>
  <div class="card failure"><?php echo bira_h($failure); ?></div>
<?php endif; ?>
<?php if ($notice !== ''): ?>
  <div class="card notice"><?php echo bira_h($notice); ?></div>
<?php endif; ?>

<div class="card">
  <h1>A4両面のビラをつくります</h1>
  <p style="margin:0; color:var(--muted); font-size:14px">
    下の3つを埋めて、いちばん下のボタンを押してください。表と裏がそろったPDFが別のタブで開きます。
    いまは川崎版が入っています。<strong>よその市の方は書き換えてください。</strong>
  </p>
</div>

<form method="post" action="/tools/bira/build.php" target="_blank">
  <input type="hidden" name="token" value="<?php echo bira_h($token); ?>">

  <!-- ステップ1 -->
  <div class="card">
    <div class="step"><b>1</b><h2>どこの、何号か</h2></div>
    <p class="lead">表面はこの4つを入れ替えるだけで完成します。中身は全国共通です。</p>
    <div class="fields grid2">
      <?php
      $head = [
          '地域' => ['市区町村の名前', '例：川崎'],
          '紙名' => ['機関紙の名前', '例：誇れる川崎'],
          '号数' => ['号数', '例：第1号'],
          '年月' => ['発行する年と月', '例：2026年10月'],
      ];
      foreach ($head as $name => [$label, $hint]):
          $bad = (string)($errors[$name] ?? '');
      ?>
      <div class="field<?php echo $bad !== '' ? ' is-bad' : ''; ?>">
        <label for="f-<?php echo bira_h($name); ?>"><?php echo bira_h($label); ?></label>
        <p class="hint"><?php echo bira_h($hint); ?></p>
        <input id="f-<?php echo bira_h($name); ?>" name="<?php echo bira_h($name); ?>" type="text"
               value="<?php echo bira_h((string)$defaults[$name]); ?>"
               maxlength="<?php echo BIRA_FIELD_MAX_LENGTH; ?>" required>
        <?php if ($bad !== ''): ?><p class="bad"><?php echo bira_h($bad); ?></p><?php endif; ?>
      </div>
      <?php endforeach; ?>
    </div>
  </div>

  <!-- ステップ2 -->
  <div class="card">
    <div class="step"><b>2</b><h2>裏面に書きたいこと</h2></div>
    <p class="lead">
      ここに書いた文章から、裏面をAIが組みます。<strong>ここに書いていないことは書かれません。</strong>
      AIが市の資料を調べることはできないので、数字や条文はご自分で貼ってください。
    </p>
    <div class="fields">
      <?php
      $textFields = [
          '大見出し' => ['裏面の見出し', '例：川崎で、これをやります', 'input'],
          '主張したいこと' => ['やりたいこと', "1行に1つ、箇条書きで結構です。4つくらいがちょうど収まります。\n例：法人市民税の超過課税をなくす", 'textarea'],
          '根拠資料' => ['市の資料から引いた数字や原文', "市のサイトや事務事業評価から、そのまま貼ってください。\nここにない数字をAIが書くことはありません。根拠のない項目は、根拠なしのまま出ます。", 'textarea'],
          'レイアウトの希望' => ['見た目の希望（なくても構いません）', '例：赤枠の導入文を上に置き、その下に番号つきで並べる', 'textarea'],
      ];
      foreach ($textFields as $name => [$label, $hint, $kind]):
          $bad = (string)($errors[$name] ?? '');
      ?>
      <div class="field<?php echo $bad !== '' ? ' is-bad' : ''; ?>">
        <label for="f-<?php echo bira_h($name); ?>"><?php echo bira_h($label); ?></label>
        <p class="hint"><?php echo nl2br(bira_h($hint)); ?></p>
        <?php if ($kind === 'input'): ?>
          <input id="f-<?php echo bira_h($name); ?>" name="<?php echo bira_h($name); ?>" type="text"
                 value="<?php echo bira_h((string)$defaults[$name]); ?>"
                 maxlength="<?php echo BIRA_BACK_TEXT_FIELDS[$name]; ?>">
        <?php else: ?>
          <textarea id="f-<?php echo bira_h($name); ?>" name="<?php echo bira_h($name); ?>"
                    maxlength="<?php echo BIRA_BACK_TEXT_FIELDS[$name]; ?>"><?php echo bira_h((string)$defaults[$name]); ?></textarea>
        <?php endif; ?>
        <?php if ($bad !== ''): ?><p class="bad"><?php echo bira_h($bad); ?></p><?php endif; ?>
      </div>
      <?php endforeach; ?>
    </div>
  </div>

  <!-- ステップ3 -->
  <div class="card">
    <div class="step"><b>3</b><h2>あなたの連絡先</h2></div>
    <p class="lead">
      裏面の下に載ります。<strong>他人の団体名のまま配ってはいけません。</strong>
      ここはAIに書かせず、入力したとおりに出します。
    </p>
    <div class="fields">
      <?php
      $footFields = [
          '氏名' => ['氏名', ''],
          '発行者' => ['発行者（資金管理団体の正式名称）', '法律で必要な表示です。ご自分の団体名にしてください。'],
          '住所' => ['住所', ''],
          '電話' => ['電話', ''],
          'メール' => ['メール', ''],
          'ＳＮＳ' => ['SNSなど（なくても構いません）', ''],
      ];
      foreach ($footFields as $name => [$label, $hint]):
          $bad = (string)($errors[$name] ?? '');
      ?>
      <div class="field<?php echo $bad !== '' ? ' is-bad' : ''; ?>">
        <label for="f-<?php echo bira_h($name); ?>"><?php echo bira_h($label); ?></label>
        <?php if ($hint !== ''): ?><p class="hint"><?php echo bira_h($hint); ?></p><?php endif; ?>
        <input id="f-<?php echo bira_h($name); ?>" name="<?php echo bira_h($name); ?>" type="text"
               value="<?php echo bira_h((string)$defaults[$name]); ?>"
               maxlength="<?php echo BIRA_BACK_TEXT_FIELDS[$name]; ?>">
        <?php if ($bad !== ''): ?><p class="bad"><?php echo bira_h($bad); ?></p><?php endif; ?>
      </div>
      <?php endforeach; ?>

      <label class="check">
        <input type="checkbox" name="confirmed" value="1">
        <span><strong>発行者が自分の資金管理団体の正式名称になっていることを確認しました。</strong>
          政治団体の届出がまだなら、先に届け出てください（政治資金規正法8条）。</span>
      </label>
    </div>
  </div>

  <!-- 実行 -->
  <div class="card go">
    <button type="submit" name="action" value="build" <?php echo $remaining <= 0 ? 'disabled' : ''; ?>>
      ビラをつくる
    </button>
    <p class="sub">
      <?php if ($remaining > 0): ?>
        表と裏がそろったPDFが、別のタブで開きます。<br>
        きょうの残り <strong><?php echo (int)$remaining; ?></strong> 回 / <?php echo BIRA_DAILY_LIMIT; ?> 回（日本時間0時に戻ります）。
        書いた文章を変えなければ、何度押しても回数は減りません。
      <?php else: ?>
        きょうの回数（<?php echo BIRA_DAILY_LIMIT; ?>回）を使い切りました。日本時間0時に戻ります。
      <?php endif; ?>
    </p>
  </div>
</form>

  <div class="card note">
    <h2>AIに事実を作らせません</h2>
    <p style="margin:0">
      裏面に載る数字や条文は、<strong>「市の資料から引いた数字や原文」に貼ったものだけ</strong>です。
      根拠を貼っていない項目は、AIが埋めずに根拠なしのまま出します。
      事実と違うビラを配ることは、政治活動として致命的だからです。
      <strong>できあがったPDFは、配る前に必ずご自分で確かめてください。</strong>
    </p>
  </div>

  <div class="card note">
    <h2>配る前に必ず確認してください</h2>
    <ul>
      <li><strong>「政治活動用」の表記は消さないでください。</strong></li>
      <li>政治団体の届出がまだなら、先に届け出てください。届出前は寄附の受領も支出もできません（政治資金規正法8条）。</li>
      <li><strong>選挙の告示が出たら、配るのをやめてください</strong>（公職選挙法142条）。貼り出しにも使えません（同143条16項）。</li>
      <li>「投票をお願いします」などの文言を入れないでください。事前運動にあたります（同129条）。</li>
      <li>号数を入れて続けて出すことに意味があります。第2号、第3号と続けてください。</li>
    </ul>
  </div>

</div>
</body>
</html>
