export function escapeHtml(s) {
	if (s == null) return '';
	return String(s).replace(/[&<>"']/g, ch => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch]));
}

export function statusLabel(s) {
	const m = { pending: '未着手', in_progress: '着手', done: '掲示', issue: '異常' };
	return m[s] || s;
}

export function statusEmoji(s) {
	switch (s) {
		case 'in_progress': return '⏳';
		case 'done': return '✅';
		case 'issue': return '⚠️';
		case 'pending':
		default: return '';
	}
}

export function removeStatusClasses(el) {
	el.classList.remove('status-pending', 'status-in_progress', 'status-done', 'status-issue');
}

export function updateMarkerStatus(code, status, isSelf, hasComment, codeToMarker) {
	const mk = codeToMarker.get(code);
	if (!mk) return;
	const el = mk.getElement();
	if (!el) return;
	const label = el.querySelector('.label');
	if (!label) return;
	const emo = label.querySelector('.status-emoji');
	removeStatusClasses(label);
	if (status) label.classList.add(`status-${status}`);
	if (emo) emo.textContent = statusEmoji(status || 'pending');
  
	let badge = label.querySelector('.self-icon');
	if (isSelf && status !== 'pending') {
		if (!badge) {
			badge = document.createElement('span');
			badge.className = 'self-icon';
			badge.textContent = '👤';
			label.appendChild(badge);
		}
	} else if (badge) {
		badge.remove();
	}
  
	let cBadge = label.querySelector('.comment-icon');
	if (hasComment) {
		if (!cBadge) {
			cBadge = document.createElement('span');
			cBadge.className = 'comment-icon';
			cBadge.textContent = '💬';
			label.appendChild(cBadge);
		}
	} else if (cBadge) {
		cBadge.remove();
	}
}

export function updatePopupStatusStyle(popup, status) {
	if (!popup) return;
	const el = popup.getElement();
	if (!el) return;
	el.classList.remove('popup-pending', 'popup-in_progress', 'popup-done', 'popup-issue');
	if (status) el.classList.add(`popup-${status}`);
}

export function renderCounts(totals, mine) {
		const set = (id, val) => { const el = document.getElementById(id); if (el) el.textContent = val; };
		set('legend-count-in_progress', `${totals.in_progress} / ${mine.in_progress}`);
		set('legend-count-done', `${totals.done} / ${mine.done}`);
		set('legend-count-issue', `${totals.issue} / ${mine.issue}`);
}

export function updateAuthUi(authState, adjustMode) {
		const name = document.getElementById('auth-name');
		const login = document.getElementById('auth-login');
		const logout = document.getElementById('auth-logout');
		const preLogin = document.getElementById('prelogin-info');
		const offsetToggle = document.getElementById('offset-toggle');
    
		if (authState && authState.loggedIn) {
				name.textContent = authState.user && authState.user.name ? `ようこそ、${authState.user.name} さん` : 'ログイン中';
				name.style.display = '';
				logout.style.display = '';
				login.style.display = 'none';
				if (preLogin) preLogin.style.display = 'none';
				if (offsetToggle) { offsetToggle.disabled = false; offsetToggle.title = 'ドラッグでラベル位置を調整'; }
		} else {
				name.style.display = 'none';
				logout.style.display = 'none';
				login.style.display = '';
				if (preLogin) preLogin.style.display = '';
				if (offsetToggle) { offsetToggle.disabled = true; offsetToggle.title = 'ログインで利用できます'; }
		}
}

