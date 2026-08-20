<?php
declare(strict_types=1);

/**
 * YouTube アップロードツール（管理者専用）のページ。
 * 動画を選び、メタデータを入力してアップロードする。サーバー側で音声正規化してから
 * YouTube へ送る。進捗はこのページでポーリング表示する。
 */

require_once dirname(__DIR__, 2) . DIRECTORY_SEPARATOR . 'lib'
    . DIRECTORY_SEPARATOR . 'youtube' . DIRECTORY_SEPARATOR . 'runtime.php';
require_once dirname(__DIR__, 2) . DIRECTORY_SEPARATOR . 'lib'
    . DIRECTORY_SEPARATOR . 'site_assets.php';

$user = youtube_require_admin(false);
$csrf = youtube_csrf_token();
$configured = youtube_is_configured();
$jobs = youtube_list_jobs(15);

function yt_state_label(string $state): string
{
    return [
        'receiving' => '受信中',
        'queued' => '待機中',
        'normalizing' => '音声正規化中',
        'uploading' => 'アップロード中',
        'done' => '完了',
        'error' => 'エラー',
    ][$state] ?? $state;
}
?><!DOCTYPE html>
<html lang="ja">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <meta name="robots" content="noindex, nofollow" />
  <title>YouTube アップロード（管理者）</title>
  <?php echo site_render_favicon_links(); ?>
  <style>
    :root { --bg:#f6f8fb; --card:#fff; --text:#222; --muted:#667788; --accent:#275ea3; --line:#06c755; --err:#ef4444; --ok:#10b981; }
    body { margin:0; font-family:-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Noto Sans JP', Meiryo, Arial, sans-serif; background:var(--bg); color:var(--text); }
    header { display:flex; align-items:center; justify-content:space-between; gap:10px; padding:12px 16px; background:#fff; box-shadow:0 1px 4px rgba(0,0,0,0.06); }
    header .title { font-weight:700; }
    header .links a { color:var(--accent); text-decoration:none; margin-left:12px; }
    .container { max-width:760px; margin:18px auto; padding:0 12px; }
    .card { background:var(--card); border-radius:10px; box-shadow:0 2px 8px rgba(0,0,0,0.06); padding:18px; margin-bottom:16px; }
    .card h2 { margin:0 0 12px; font-size:16px; }
    .field { margin-bottom:14px; }
    .field label { display:block; font-size:13px; color:var(--muted); font-weight:600; margin-bottom:4px; }
    .field input[type=text], .field textarea, .field select, .field input[type=datetime-local], .field input[type=number] {
      width:100%; padding:9px 11px; border:1px solid #d1d5db; border-radius:8px; font-size:14px; box-sizing:border-box; font-family:inherit; }
    .field textarea { min-height:90px; resize:vertical; }
    .row { display:flex; gap:12px; flex-wrap:wrap; }
    .row > .field { flex:1; min-width:150px; }
    .check { display:flex; align-items:center; gap:8px; font-size:14px; margin-bottom:8px; }
    .hint { font-size:12px; color:var(--muted); margin-top:3px; }
    .btn { display:inline-block; padding:11px 22px; border:none; border-radius:8px; font-weight:700; font-size:15px; cursor:pointer; }
    .btn.primary { background:var(--accent); color:#fff; }
    .btn.primary:disabled { background:#9ca3af; cursor:not-allowed; }
    .filepick { border:2px dashed #cbd5e1; border-radius:10px; padding:20px; text-align:center; color:var(--muted); cursor:pointer; transition:background .15s, border-color .15s; }
    .filepick.has-file { border-color:var(--accent); color:var(--text); }
    .filepick.dragover { border-color:var(--accent); background:#eef4fb; color:var(--accent); }
    .normalize-field { background:#f7fafc; border:1px solid #e6edf5; border-radius:8px; padding:12px; }
    .normalize-field label.check { margin-bottom:0; }
    #loudnorm-params.disabled { opacity:.45; pointer-events:none; }
    .advanced { border-top:1px solid #eef2f7; margin-top:6px; padding-top:12px; }
    .advanced summary { cursor:pointer; font-size:13px; color:var(--accent); }
    #progress-card { display:none; }
    .bar { height:12px; background:#e6edf5; border-radius:999px; overflow:hidden; margin:8px 0; }
    .bar > span { display:block; height:100%; width:0; background:var(--accent); transition:width .3s; }
    .warn { background:#fef2f2; border:1px solid #fca5a5; color:#b91c1c; padding:10px 12px; border-radius:8px; font-size:13px; }
    table.jobs { width:100%; border-collapse:collapse; font-size:13px; }
    table.jobs th, table.jobs td { text-align:left; padding:8px 6px; border-bottom:1px solid #eef2f7; }
    .pill { display:inline-block; padding:2px 8px; border-radius:999px; font-size:12px; background:#eef4fb; color:var(--accent); }
    .pill.done { background:#ecfdf5; color:var(--ok); }
    .pill.error { background:#fef2f2; color:var(--err); }
    .msg { font-size:13px; color:var(--muted); }
  </style>
</head>
<body>
  <script>if(/Line\//i.test(navigator.userAgent)&&!location.search.includes('openExternalBrowser=1')){var u=new URL(location.href);u.searchParams.set('openExternalBrowser','1');location.replace(u.toString());}</script>
  <header>
    <div class="title">YouTube アップロード（管理者）</div>
    <div class="links">
      <a href="/line/profile.php">アカウント</a>
      <a href="/">トップ</a>
    </div>
  </header>

  <div class="container">
    <?php if (!$configured): ?>
      <div class="card"><div class="warn">
        YouTube のトークンが未設定です。<code>data/youtube/oauth-token.json</code> を配置してください。
        アップロードは実行できません。
      </div></div>
    <?php endif; ?>

    <div class="card" id="form-card">
      <h2>動画をアップロード</h2>
      <p class="hint">選んだ動画はサーバー側で音声を正規化（loudnorm 二段階）してから YouTube へ送ります。既定は「非公開」です。</p>

      <div class="field">
        <div class="filepick" id="filepick">
          <input type="file" id="file" accept="video/mp4,.mp4" style="display:none">
          <div id="filepick-label">ここに .mp4 をドラッグ＆ドロップ、またはクリックして選択</div>
        </div>
        <div class="hint">対応: MP4（H.264 映像 + 音声）。上限 5 GiB。音声正規化をオフにすると、ディスクに貯めず YouTube へ直接ストリーム送信します。</div>
      </div>

      <div class="field">
        <label>サムネイル（任意）</label>
        <div class="filepick" id="thumbpick">
          <input type="file" id="thumb" accept="image/jpeg,image/png,.jpg,.jpeg,.png" style="display:none">
          <div id="thumbpick-label">ここに画像をドラッグ＆ドロップ、またはクリックして選択</div>
        </div>
        <div class="hint">JPG / PNG・2MiB以内。アップロード後に YouTube のサムネイルへ設定します。</div>
      </div>

      <div class="field">
        <label for="title">タイトル（必須・100文字以内）</label>
        <input type="text" id="title" maxlength="100" placeholder="動画のタイトル">
      </div>

      <div class="field">
        <label for="description">説明</label>
        <textarea id="description" placeholder="動画の説明"></textarea>
      </div>

      <div class="row">
        <div class="field">
          <label for="privacy">公開設定</label>
          <select id="privacy">
            <option value="private" selected>非公開</option>
            <option value="unlisted">限定公開</option>
            <option value="public">公開</option>
          </select>
        </div>
        <div class="field">
          <label for="tags">タグ（カンマ区切り）</label>
          <input type="text" id="tags" placeholder="例: 川崎, 政治">
        </div>
      </div>

      <div class="field normalize-field">
        <label class="check"><input type="checkbox" id="normalize" checked> <strong>音声を正規化する</strong>（推奨）</label>
        <div class="hint">オフにすると音声はそのまま（無加工）でアップロードします。細かい目標値は「詳細設定」で調整できます。</div>
      </div>

      <details class="advanced">
        <summary>詳細設定</summary>
        <div style="margin-top:12px;">
          <div class="hint" id="loudnorm-hint" style="margin-bottom:8px;">音声正規化の目標値（正規化オンのときだけ有効）。</div>
          <div class="row" id="loudnorm-params">
            <div class="field">
              <label for="loudness">目標音量 LUFS</label>
              <input type="number" id="loudness" value="-14" step="0.5" min="-70" max="-5">
            </div>
            <div class="field">
              <label for="true_peak">最大ピーク dBTP</label>
              <input type="number" id="true_peak" value="-1" step="0.1" min="-9" max="0">
            </div>
            <div class="field">
              <label for="lra">LRA</label>
              <input type="number" id="lra" value="11" step="1" min="1" max="20">
            </div>
          </div>
          <div class="field">
            <label for="category">カテゴリID</label>
            <input type="text" id="category" value="22">
            <div class="hint">既定 22（People &amp; Blogs）。</div>
          </div>
          <div class="field">
            <label for="publish_at">公開予約（非公開のときのみ・任意）</label>
            <input type="datetime-local" id="publish_at">
          </div>
          <label class="check"><input type="checkbox" id="made_for_kids"> 子ども向けコンテンツ</label>
          <label class="check"><input type="checkbox" id="synthetic_media"> 合成/改変されたメディアを含む</label>
          <label class="check"><input type="checkbox" id="notify_subscribers"> 登録者へ通知する</label>
        </div>
      </details>

      <div style="margin-top:8px;">
        <button class="btn primary" id="submit" <?php echo $configured ? '' : 'disabled'; ?>>アップロード開始</button>
        <span class="msg" id="form-msg"></span>
      </div>
    </div>

    <div class="card" id="progress-card">
      <h2>進捗</h2>
      <div class="bar"><span id="bar"></span></div>
      <div class="msg" id="progress-msg">準備中…</div>
      <div id="progress-result" style="margin-top:10px;"></div>
    </div>

    <div class="card">
      <h2>最近のジョブ</h2>
      <?php if (!$jobs): ?>
        <p class="msg">まだありません。</p>
      <?php else: ?>
        <table class="jobs">
          <thead><tr><th>タイトル</th><th>公開</th><th>状態</th><th>結果</th></tr></thead>
          <tbody>
          <?php foreach ($jobs as $j): ?>
            <tr>
              <td><?php echo youtube_h($j['title'] !== '' ? $j['title'] : '(無題)'); ?></td>
              <td><?php echo youtube_h($j['privacy']); ?></td>
              <td>
                <span class="pill <?php echo youtube_h($j['state']); ?>"><?php echo youtube_h(yt_state_label($j['state'])); ?></span>
                <?php if (in_array($j['state'], ['normalizing', 'uploading'], true)): ?>
                  <?php echo (int) $j['progress']; ?>%
                <?php endif; ?>
              </td>
              <td>
                <?php if ($j['watch_url'] !== ''): ?>
                  <a href="<?php echo youtube_h($j['watch_url']); ?>" target="_blank" rel="noopener noreferrer">視聴</a>
                <?php elseif ($j['state'] === 'error'): ?>
                  <span class="msg"><?php echo youtube_h($j['message']); ?></span>
                <?php else: ?>
                  <span class="msg">—</span>
                <?php endif; ?>
              </td>
            </tr>
          <?php endforeach; ?>
          </tbody>
        </table>
      <?php endif; ?>
    </div>
  </div>

  <script>
    const CSRF = <?php echo json_encode($csrf); ?>;
    const fileInput = document.getElementById('file');
    const filepick = document.getElementById('filepick');
    const filepickLabel = document.getElementById('filepick-label');
    const submitBtn = document.getElementById('submit');
    const formMsg = document.getElementById('form-msg');
    const progressCard = document.getElementById('progress-card');
    const bar = document.getElementById('bar');
    const progressMsg = document.getElementById('progress-msg');
    const progressResult = document.getElementById('progress-result');

    const normalizeToggle = document.getElementById('normalize');
    const loudnormParams = document.getElementById('loudnorm-params');
    function syncNormalizeUi() {
      if (loudnormParams) loudnormParams.classList.toggle('disabled', !normalizeToggle.checked);
    }
    normalizeToggle.addEventListener('change', syncNormalizeUi);
    syncNormalizeUi();

    // 選択・ドロップ双方でここに入れる。以降の送信はこの currentFile を使う。
    let currentFile = null;
    function isMp4(file) {
      return file && (/\.mp4$/i.test(file.name) || file.type === 'video/mp4' || file.type === '');
    }
    function setFile(f) {
      if (!f) return;
      if (!isMp4(f)) { formMsg.textContent = 'MP4（.mp4）を選んでください。'; return; }
      currentFile = f;
      formMsg.textContent = '';
      filepick.classList.add('has-file');
      filepickLabel.textContent = f.name + '（' + (f.size / 1048576).toFixed(1) + ' MB）';
      if (!document.getElementById('title').value) {
        document.getElementById('title').value = f.name.replace(/\.[^.]+$/, '');
      }
    }

    // ドラッグ＆ドロップの共通配線。
    function wireDnd(zone, onFile) {
      zone.addEventListener('click', () => zone.querySelector('input[type=file]').click());
      ['dragenter', 'dragover'].forEach((ev) =>
        zone.addEventListener(ev, (e) => { e.preventDefault(); e.stopPropagation(); zone.classList.add('dragover'); }));
      ['dragleave', 'dragend', 'drop'].forEach((ev) =>
        zone.addEventListener(ev, (e) => { e.preventDefault(); e.stopPropagation(); zone.classList.remove('dragover'); }));
      zone.addEventListener('drop', (e) => {
        const files = e.dataTransfer && e.dataTransfer.files;
        if (files && files.length) onFile(files[0]);
      });
    }

    wireDnd(filepick, setFile);
    fileInput.addEventListener('change', () => setFile(fileInput.files[0]));

    // サムネイル
    let currentThumb = null;
    const thumbInput = document.getElementById('thumb');
    const thumbLabel = document.getElementById('thumbpick-label');
    function setThumb(f) {
      if (!f) return;
      if (!/\.(jpe?g|png)$/i.test(f.name) && !/^image\/(jpeg|png)$/.test(f.type)) {
        formMsg.textContent = 'サムネイルは JPG / PNG を選んでください。'; return;
      }
      if (f.size > 2 * 1024 * 1024) { formMsg.textContent = 'サムネイルは2MiB以内にしてください。'; return; }
      currentThumb = f;
      document.getElementById('thumbpick').classList.add('has-file');
      thumbLabel.textContent = f.name + '（' + (f.size / 1024).toFixed(0) + ' KB）';
    }
    wireDnd(document.getElementById('thumbpick'), setThumb);
    thumbInput.addEventListener('change', () => setThumb(thumbInput.files[0]));

    // ページ全体でのドロップは既定動作（ブラウザが開く）を抑止する。
    ['dragover', 'drop'].forEach((ev) =>
      window.addEventListener(ev, (e) => { e.preventDefault(); }));

    async function uploadThumbnail(jobId, thumb) {
      const ext = /\.png$/i.test(thumb.name) || thumb.type === 'image/png' ? 'png' : 'jpg';
      const buf = await thumb.arrayBuffer();
      const res = await fetch('/youtube/api.php?action=thumbnail&job=' + jobId + '&ext=' + ext, {
        method: 'POST',
        headers: { 'X-YouTube-CSRF': CSRF, 'Content-Type': 'application/octet-stream' },
        body: buf,
      });
      if (!res.ok) {
        const d = await res.json().catch(() => ({}));
        throw new Error(d.error || 'サムネイル送信に失敗');
      }
    }

    async function postJson(action, payload) {
      const res = await fetch('/youtube/api.php?action=' + action, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.error || ('HTTP ' + res.status));
      return data;
    }

    function collectMeta() {
      const tags = document.getElementById('tags').value.split(',').map(t => t.trim()).filter(Boolean);
      const meta = {
        csrf: CSRF,
        title: document.getElementById('title').value.trim(),
        description: document.getElementById('description').value,
        privacy_status: document.getElementById('privacy').value,
        tags,
        category_id: document.getElementById('category').value.trim() || '22',
        normalize: document.getElementById('normalize').checked,
        loudness: parseFloat(document.getElementById('loudness').value),
        true_peak: parseFloat(document.getElementById('true_peak').value),
        lra: parseFloat(document.getElementById('lra').value),
        notify_subscribers: document.getElementById('notify_subscribers').checked,
      };
      if (document.getElementById('made_for_kids').checked) meta.made_for_kids = true;
      if (document.getElementById('synthetic_media').checked) meta.synthetic_media = true;
      const publishAt = document.getElementById('publish_at').value;
      if (publishAt) meta.publish_at = publishAt;
      return meta;
    }

    async function uploadChunks(jobId, file, chunkSize) {
      let offset = 0;
      while (offset < file.size) {
        const slice = file.slice(offset, offset + chunkSize);
        const buf = await slice.arrayBuffer();
        const res = await fetch('/youtube/api.php?action=chunk&job=' + jobId + '&offset=' + offset, {
          method: 'POST',
          headers: { 'X-YouTube-CSRF': CSRF, 'Content-Type': 'application/octet-stream' },
          body: buf,
        });
        const data = await res.json().catch(() => ({}));
        if (res.status === 409 && typeof data.expected_offset === 'number') {
          offset = data.expected_offset; // 位置を合わせて再送
          continue;
        }
        if (!res.ok) throw new Error(data.error || ('チャンク送信に失敗 HTTP ' + res.status));
        offset += slice.size;
        const pct = Math.round((offset / file.size) * 100);
        bar.style.width = pct + '%';
        progressMsg.textContent = 'アップロード送信中… ' + pct + '%';
      }
      return offset;
    }

    let pollTimer = null;
    async function pollStatus(jobId) {
      try {
        const res = await fetch('/youtube/api.php?action=status&job=' + jobId);
        const data = await res.json();
        const st = data.status || {};
        if (typeof st.progress === 'number') bar.style.width = st.progress + '%';
        progressMsg.textContent = st.message || st.state || '処理中…';
        if (st.state === 'done') {
          clearInterval(pollTimer);
          bar.style.width = '100%';
          progressResult.innerHTML = '<div style="color:#10b981;font-weight:700">完了しました。</div>' +
            (st.watch_url ? '<a href="' + st.watch_url + '" target="_blank" rel="noopener noreferrer">' + st.watch_url + '</a>' : '');
          setTimeout(() => location.reload(), 2500);
        } else if (st.state === 'error') {
          clearInterval(pollTimer);
          progressResult.innerHTML = '<div class="warn">エラー: ' + (st.error || st.message || '不明') + '</div>';
          submitBtn.disabled = false;
        }
      } catch (e) { /* 次のポーリングで回復 */ }
    }

    submitBtn.addEventListener('click', async () => {
      const file = currentFile;
      if (!file) { formMsg.textContent = '動画を選んでください。'; return; }
      const meta = collectMeta();
      meta.size = file.size;
      if (!meta.title) { formMsg.textContent = 'タイトルを入力してください。'; return; }

      submitBtn.disabled = true;
      formMsg.textContent = '';
      progressCard.style.display = 'block';
      progressResult.innerHTML = '';
      bar.style.width = '0%';
      progressMsg.textContent = 'ジョブを作成しています…';

      try {
        const created = await postJson('create', meta);
        if (currentThumb) {
          progressMsg.textContent = 'サムネイルを送信しています…';
          await uploadThumbnail(created.job_id, currentThumb);
        }
        progressMsg.textContent = created.mode === 'stream'
          ? 'YouTube へ直接ストリーム送信しています…'
          : '動画を送信しています…';
        const total = await uploadChunks(created.job_id, file, created.chunk_size);
        progressMsg.textContent = '仕上げ処理をしています…';
        await postJson('finalize', { csrf: CSRF, job: created.job_id, size: total });
        pollTimer = setInterval(() => pollStatus(created.job_id), 2000);
        pollStatus(created.job_id);
      } catch (e) {
        progressResult.innerHTML = '<div class="warn">失敗: ' + (e.message || e) + '</div>';
        submitBtn.disabled = false;
      }
    });
  </script>
</body>
</html>
