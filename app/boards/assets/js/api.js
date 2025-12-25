export async function fetchTaskStatus(slug, code) {
  try {
	const res = await fetch(`/boards/api/tasks.php?slug=${encodeURIComponent(slug)}&code=${encodeURIComponent(code)}`);
	if (!res.ok) return null;
	return await res.json();
  } catch (_) { return null; }
}

export async function fetchStats(slug) {
	const res = await fetch(`/boards/api/stats.php?slug=${encodeURIComponent(slug)}`, { cache: 'no-store' });
	if (!res.ok) throw new Error('統計データの取得に失敗しました (HTTP ' + res.status + ')');
	return await res.json();
}

export async function fetchQuery(slug, params) {
	params.append('slug', slug);
	const res = await fetch('/boards/api/query.php?' + params.toString());
	if (!res.ok) throw new Error('データの取得に失敗しました (HTTP ' + res.status + ')');
	return await res.json();
}

export async function setStatus(slug, code, status, note) {
	const res = await fetch('/boards/api/tasks.php', {
		method: 'POST', headers: { 'Content-Type': 'application/json' },
		body: JSON.stringify({ action: 'set_status', slug, board_code: code, status, note })
	});
	if (res.redirected) { window.location.href = res.url; return null; }
	if (!res.ok) throw new Error('ステータスの更新に失敗しました');
	return await res.json();
}

export async function postComment(slug, code, note) {
	const res = await fetch('/boards/api/tasks.php', {
		method: 'POST', headers: { 'Content-Type': 'application/json' },
		body: JSON.stringify({ action: 'comment', slug, board_code: code, note })
	});
	if (res.redirected) { window.location.href = res.url; return null; }
	if (!res.ok) throw new Error('コメントの投稿に失敗しました');
	return await res.json();
}

export async function moveMarker(slug, code, lat, lon) {
	const res = await fetch('/boards/api/tasks.php', {
		method: 'POST', headers: { 'Content-Type': 'application/json' },
		body: JSON.stringify({ action: 'move', slug, board_code: code, lat, lon })
	});
	if (res.redirected) { window.location.href = res.url; return null; }
	if (!res.ok) throw new Error('HTTP ' + res.status);
	return await res.json();
}

export async function checkAuth(slug) {
	const url = slug ? `/line/status.php?slug=${encodeURIComponent(slug)}` : '/line/status.php';
	const r = await fetch(url, { cache: 'no-store' });
	return await r.json();
}

