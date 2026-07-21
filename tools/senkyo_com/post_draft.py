#!/usr/bin/env python3
"""選挙ドットコム（ボネクタ管理画面）へブログ下書きを準備する。

ルートの secret.json で自動ログインし、Markdown 原稿と画像を新規投稿画面へ入力する。
公開ボタンは操作せず、--save-draft を明示した場合だけ「下書き保存」と明記された
ボタンを押す。
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urljoin

from playwright.sync_api import (
    BrowserContext,
    Error as PlaywrightError,
    Locator,
    Page,
    Playwright,
    TimeoutError as PlaywrightTimeoutError,
    sync_playwright,
)


LOGIN_URL = "https://www.go2senkyo.com/dusers/sign_in"
DASHBOARD_URL = "https://www.go2senkyo.com/cms"
DEFAULT_PROFILE_DIR = Path("work/senkyo-com/browser-profile")
DEFAULT_ARTIFACT_DIR = Path("work/senkyo-com/artifacts")
DEFAULT_SECRET_PATH = Path("secret.json")

DEFAULT_SELECTORS: dict[str, list[str]] = {
    "title": [
        "input[name='post[title]']",
        "input[name='blog[title]']",
        "input[name*='[title]']",
        "input#post_title",
        "input#blog_title",
        "input[name='title']",
    ],
    "body": [
        "textarea[name='post[body]']",
        "textarea[name='blog[body]']",
        "textarea[name*='[body]']",
        "textarea[name*='[content]']",
        "textarea#post_body",
        "textarea#blog_body",
        "textarea[name='body']",
        "[contenteditable='true'][role='textbox']",
        "[contenteditable='true']",
    ],
    "image": [
        "input[name='post[thumbnail]']",
        "input#file-input",
        "input[type='file'][name*='image']",
        "input[type='file'][name*='photo']",
        "input[type='file'][accept*='image']",
        "input[type='file']",
    ],
}

SECTION_LINK_LABELS = (
    "活動記録",
    "ブログ",
    "ブログ投稿",
    "投稿管理",
    "記事管理",
    "記事一覧",
)
NEW_POST_LABELS = (
    "活動記録を作成",
    "ブログを書く",
    "記事を書く",
    "新規投稿",
    "新規作成",
    "記事を作成",
)
DRAFT_BUTTON_LABELS = (
    "下書き保存",
    "下書きとして保存",
)
POST_EDIT_URL_PATTERN = re.compile(
    r"^https://www\.go2senkyo\.com/cms/politicians/\d+/posts/[^/?#]+/edit/?$"
)
NEW_POST_URL_PATTERN = re.compile(
    r"^https://www\.go2senkyo\.com/cms/politicians/\d+/posts/new/?$"
)
MAX_IMAGE_BYTES = 13 * 1024 * 1024
SUPPORTED_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".gif"}
IMAGE_BUTTON_LABELS = (
    "画像を選択",
    "写真を選択",
    "画像を追加",
    "アイキャッチ画像",
    "メイン画像",
)
CONTENT_IMAGE_BUTTON_SELECTOR = "button[data-cke-tooltip-text='画像挿入']"
CONTENT_IMAGE_UPLOAD_URL_FRAGMENT = "ckeditor5/pictures"
IMAGE_MARKDOWN_PATTERN = re.compile(r"^!\[([^\]]*)\]\(([^)]+)\)$")


class SenkyoComError(RuntimeError):
    """利用者が修正できる投稿エラー。"""


@dataclass(frozen=True)
class Article:
    title: str
    markdown: str
    body_markdown: str
    body_html: str


@dataclass(frozen=True)
class Credentials:
    cms_url: str
    login_id: str
    password: str


@dataclass
class BrowserSession:
    playwright: Playwright
    context: BrowserContext
    page: Page

    def close(self) -> None:
        self.context.close()
        self.playwright.stop()


def extract_article(path: Path, title_override: str = "") -> Article:
    if not path.is_file():
        raise SenkyoComError(f"記事ファイルが見つかりません: {path}")
    markdown = path.read_text(encoding="utf-8-sig").strip()
    if not markdown:
        raise SenkyoComError(f"記事ファイルが空です: {path}")

    lines = markdown.splitlines()
    detected_title = ""
    body_start = 0
    for index, line in enumerate(lines):
        if not line.strip():
            continue
        if line.startswith("# "):
            detected_title = line[2:].strip()
            body_start = index + 1
        break

    title = title_override.strip() or detected_title
    if not title:
        raise SenkyoComError("タイトルがありません。先頭を '# タイトル' にするか --title を指定してください。")

    body_markdown = "\n".join(lines[body_start:]).strip()
    if not body_markdown:
        raise SenkyoComError("記事本文がありません。")
    return Article(
        title=title,
        markdown=markdown,
        body_markdown=body_markdown,
        body_html=markdown_to_html(body_markdown, base_dir=path.parent),
    )


def inline_markdown(text: str) -> str:
    pattern = re.compile(r"\*\*(.+?)\*\*|\[([^\]]+)\]\((https?://[^\s)]+)\)")
    output: list[str] = []
    position = 0
    for match in pattern.finditer(text):
        output.append(html.escape(text[position : match.start()]))
        if match.group(1) is not None:
            output.append(f"<strong>{html.escape(match.group(1))}</strong>")
        else:
            label = html.escape(match.group(2) or "")
            url = html.escape(match.group(3) or "", quote=True)
            output.append(f'<a href="{url}" target="_blank" rel="noopener noreferrer">{label}</a>')
        position = match.end()
    output.append(html.escape(text[position:]))
    return "".join(output)


def markdown_to_html(markdown: str, base_dir: Path | None = None) -> str:
    """投稿原稿で使う見出し・箇条書き・太字・リンク・コード・画像を安全にHTML化する。

    画像行 `![説明](パス)` はローカルファイルパスのままフィギュア要素にする。
    実際のアップロードとURL差し替えは embed_content_images() が後で行う。
    """

    blocks: list[str] = []
    paragraph: list[str] = []
    list_items: list[str] = []
    code_lines: list[str] = []
    code_language = ""
    in_code_block = False

    def flush_paragraph() -> None:
        if paragraph:
            blocks.append(f"<p>{inline_markdown(' '.join(paragraph))}</p>")
            paragraph.clear()

    def flush_list() -> None:
        if list_items:
            items = "".join(f"<li>{inline_markdown(item)}</li>" for item in list_items)
            blocks.append(f"<ul>{items}</ul>")
            list_items.clear()

    def flush_code_block() -> None:
        nonlocal code_language, in_code_block
        if not in_code_block:
            return
        class_attribute = (
            f' class="language-{html.escape(code_language, quote=True)}"' if code_language else ""
        )
        escaped_code = html.escape("\n".join(code_lines))
        blocks.append(f"<pre><code{class_attribute}>{escaped_code}</code></pre>")
        code_lines.clear()
        code_language = ""
        in_code_block = False

    for raw_line in markdown.splitlines():
        if in_code_block:
            if re.fullmatch(r"```\s*", raw_line):
                flush_code_block()
            else:
                code_lines.append(raw_line)
            continue

        line = raw_line.rstrip()
        code_fence = re.fullmatch(r"```([A-Za-z0-9_+-]*)\s*", line)
        if code_fence:
            flush_paragraph()
            flush_list()
            code_language = code_fence.group(1)
            in_code_block = True
            continue
        if not line.strip():
            flush_paragraph()
            flush_list()
            continue
        if line.startswith("## "):
            flush_paragraph()
            flush_list()
            blocks.append(f"<h2>{inline_markdown(line[3:].strip())}</h2>")
            continue
        if line.startswith("### "):
            flush_paragraph()
            flush_list()
            blocks.append(f"<h3>{inline_markdown(line[4:].strip())}</h3>")
            continue
        if line.startswith("- "):
            flush_paragraph()
            list_items.append(line[2:].strip())
            continue
        image_match = IMAGE_MARKDOWN_PATTERN.match(line)
        if image_match:
            flush_paragraph()
            flush_list()
            caption = image_match.group(1).strip()
            image_path = image_match.group(2).strip()
            if base_dir is not None and not re.match(r"^https?://", image_path):
                image_path = str((base_dir / image_path).resolve())
            src = html.escape(image_path, quote=True)
            alt = html.escape(caption, quote=True)
            figcaption = f"<figcaption>{inline_markdown(caption)}</figcaption>" if caption else ""
            blocks.append(f'<figure class="image"><img src="{src}" alt="{alt}">{figcaption}</figure>')
            continue
        flush_list()
        paragraph.append(line.strip())

    flush_paragraph()
    flush_list()
    flush_code_block()
    return "\n".join(blocks)


def load_selector_config(path: Path | None) -> dict[str, list[str]]:
    config = {key: list(values) for key, values in DEFAULT_SELECTORS.items()}
    if path is None:
        return config
    if not path.is_file():
        raise SenkyoComError(f"セレクター設定が見つかりません: {path}")
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise SenkyoComError("セレクター設定のルートはJSONオブジェクトにしてください。")
    for key in config:
        value = raw.get(key)
        if value is None:
            continue
        if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
            raise SenkyoComError(f"セレクター設定 '{key}' は文字列配列にしてください。")
        config[key] = value + [item for item in config[key] if item not in value]
    return config


def load_credentials(path: Path) -> Credentials:
    if not path.is_file():
        raise SenkyoComError(f"認証情報ファイルが見つかりません: {path}")
    try:
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise SenkyoComError(f"認証情報ファイルがJSONとして不正です: {path}") from exc
    section = raw.get("go2senkyo") if isinstance(raw, dict) else None
    if not isinstance(section, dict):
        raise SenkyoComError("secret.json に go2senkyo オブジェクトがありません。")
    cms_url = str(section.get("cmsUrl") or "").strip()
    login_id = str(section.get("id") or "").strip()
    password = str(section.get("password") or "")
    if not cms_url.startswith("https://www.go2senkyo.com/"):
        raise SenkyoComError("go2senkyo.cmsUrl は https://www.go2senkyo.com/ のURLにしてください。")
    if not login_id or not password:
        raise SenkyoComError("go2senkyo.id または go2senkyo.password が空です。")
    return Credentials(cms_url=cms_url, login_id=login_id, password=password)


def credentials_for_args(args: argparse.Namespace) -> Credentials:
    """手動ログイン時はsecret.jsonを読まず、管理画面の既定URLだけを使う。"""

    if args.manual_login:
        return Credentials(cms_url=DASHBOARD_URL, login_id="", password="")
    return load_credentials(Path(args.secret))


def start_browser(args: argparse.Namespace) -> BrowserSession:
    profile_dir = Path(args.profile_dir).resolve()
    profile_dir.mkdir(parents=True, exist_ok=True)
    playwright = sync_playwright().start()
    context = playwright.chromium.launch_persistent_context(
        str(profile_dir),
        channel=args.channel,
        headless=args.headless,
        viewport={"width": 1440, "height": 1000},
        args=[
            "--disable-blink-features=AutomationControlled",
            "--disable-save-password-bubble",
            "--disable-features=PasswordManagerOnboarding,PasswordManager,PasswordLeakDetection",
        ],
    )
    context.set_default_timeout(args.timeout_ms)
    page = context.pages[0] if context.pages else context.new_page()
    return BrowserSession(playwright=playwright, context=context, page=page)


def is_login_page(page: Page) -> bool:
    if "/dusers/sign_in" in page.url:
        return True
    email = page.locator("input[type='email'], input[name*='email']")
    password = page.locator("input[type='password']")
    return email.count() > 0 and password.count() > 0


def perform_automatic_login(page: Page, credentials: Credentials) -> None:
    identifier = visible_locator(
        page,
        (
            "input[type='email']",
            "input[name*='email']",
            "input[name*='login']",
            "input[name*='id']",
        ),
    ) or find_by_label_or_placeholder(page, ("メールアドレス", "ログインID", "ID"))
    password = visible_locator(page, ("input[type='password']",)) or find_by_label_or_placeholder(
        page, ("パスワード",)
    )
    if identifier is None or password is None:
        raise SenkyoComError("ログイン入力欄を検出できませんでした。")

    identifier.fill(credentials.login_id)
    password.fill(credentials.password)

    remember = page.get_by_label("ログイン状態を保持する", exact=False)
    for index in range(remember.count()):
        candidate = remember.nth(index)
        if candidate.is_visible():
            try:
                candidate.check()
            except Exception:
                pass
            break

    if not click_unique_named(page, ("ログイン",)):
        raise SenkyoComError("ログインボタンを一意に検出できませんでした。")
    try:
        page.wait_for_load_state("networkidle", timeout=15_000)
    except PlaywrightTimeoutError:
        pass
    page.wait_for_timeout(500)
    if is_login_page(page):
        raise SenkyoComError("自動ログインに失敗しました。secret.json の認証情報を確認してください。")


def ensure_logged_in(
    page: Page,
    credentials: Credentials,
    *,
    manual_login: bool,
    headless: bool,
    no_wait: bool,
) -> None:
    if not is_login_page(page):
        return
    if not manual_login:
        print("secret.json の go2senkyo 認証情報でログインします。")
        perform_automatic_login(page, credentials)
        print("自動ログインが完了しました。")
        return
    if headless or no_wait:
        raise SenkyoComError("手動ログインでは --headless と --no-wait を外してください。")
    print("開いたブラウザーで選挙ドットコムへログインしてください。")
    wait_for_user("管理画面が表示されるまでログイン操作を完了してください。", True)
    if is_login_page(page):
        raise SenkyoComError("ログイン画面のままです。ログインを完了してからEnterを押してください。")


def visible_locator(page: Page, selectors: Iterable[str]) -> Locator | None:
    for selector in selectors:
        locator = page.locator(selector)
        for index in range(locator.count()):
            candidate = locator.nth(index)
            try:
                if candidate.is_visible():
                    return candidate
            except PlaywrightTimeoutError:
                continue
    return None


def any_locator(page: Page, selectors: Iterable[str]) -> Locator | None:
    for selector in selectors:
        locator = page.locator(selector)
        if locator.count() > 0:
            return locator.nth(0)
    return None


def find_by_label_or_placeholder(page: Page, labels: Iterable[str]) -> Locator | None:
    for label in labels:
        for locator in (
            page.get_by_label(label, exact=False),
            page.get_by_placeholder(label, exact=False),
        ):
            for index in range(locator.count()):
                candidate = locator.nth(index)
                if candidate.is_visible():
                    return candidate
    return None


def editor_fields_present(page: Page, selectors: dict[str, list[str]]) -> bool:
    title = visible_locator(page, selectors["title"]) or find_by_label_or_placeholder(
        page, ("タイトル", "記事タイトル", "見出し")
    )
    body = any_locator(page, selectors["body"])
    if body is None:
        body = visible_locator(page, ("iframe",))
    return title is not None and body is not None


def click_unique_named(page: Page, labels: Iterable[str]) -> bool:
    for label in labels:
        candidates = [
            page.get_by_role("link", name=label, exact=True),
            page.get_by_role("button", name=label, exact=True),
            page.locator(f"input[type='submit'][value='{label}']"),
        ]
        for locator in candidates:
            visible = [locator.nth(i) for i in range(locator.count()) if locator.nth(i).is_visible()]
            if len(visible) == 1:
                visible[0].click()
                try:
                    page.wait_for_load_state("domcontentloaded", timeout=10_000)
                except PlaywrightTimeoutError:
                    pass
                return True
    return False


def validate_post_edit_url(url: str) -> str:
    url = url.strip()
    if not POST_EDIT_URL_PATTERN.match(url):
        raise SenkyoComError(
            "更新する記事は https://www.go2senkyo.com/cms/politicians/<番号>/posts/<記事ID>/edit "
            "のURLで指定してください。"
        )
    return url


def normalize_new_post_url(base_url: str, href: str) -> str:
    """管理画面内で見つけた新規投稿URLを絶対URLにし、送信先を固定する。"""

    url = urljoin(base_url, href.strip())
    if not NEW_POST_URL_PATTERN.match(url):
        raise SenkyoComError(f"選挙ドットコムの新規投稿URLとして使用できません: {url}")
    return url


def find_new_post_url(page: Page) -> str | None:
    """ログイン後の画面から、アカウント固有の新規投稿URLを取得する。"""

    candidates = (
        page.get_by_role("link", name="活動記録を作成", exact=True),
        page.locator(
            "a[href*='/cms/politicians/'][href$='/posts/new'], "
            "a[href*='/cms/politicians/'][href$='/posts/new/']"
        ),
    )
    urls: set[str] = set()
    for locator in candidates:
        for index in range(locator.count()):
            candidate = locator.nth(index)
            if not candidate.is_visible():
                continue
            href = candidate.get_attribute("href") or ""
            if not href:
                continue
            try:
                urls.add(normalize_new_post_url(page.url, href))
            except SenkyoComError:
                continue
    if len(urls) > 1:
        raise SenkyoComError("新規投稿URLが複数見つかったため、安全のため処理を中止しました。")
    return next(iter(urls), None)


def find_existing_post_edit_url(page: Page, article_title: str) -> str | None:
    """現在の一覧画面に同名記事があれば、その編集URLを返す。"""

    locator = page.get_by_role("link", name=article_title, exact=True)
    urls: set[str] = set()
    for index in range(locator.count()):
        candidate = locator.nth(index)
        if not candidate.is_visible():
            continue
        href = candidate.get_attribute("href") or ""
        if not href:
            continue
        url = urljoin(page.url, href.strip())
        if POST_EDIT_URL_PATTERN.match(url):
            urls.add(url)
    if len(urls) > 1:
        raise SenkyoComError("同じタイトルの記事が複数見つかったため、安全のため処理を中止しました。")
    return next(iter(urls), None)


def reject_existing_post(page: Page, article_title: str, allow_duplicate: bool) -> None:
    if allow_duplicate:
        return
    edit_url = find_existing_post_edit_url(page, article_title)
    if edit_url is None:
        return
    raise SenkyoComError(
        "同じタイトルの記事がすでにあります。重複作成は中止しました。\n"
        f"既存記事: {edit_url}\n"
        "既存記事を直す場合は update コマンドと --post-url を使ってください。"
    )


def navigate_to_editor(
    page: Page,
    url: str,
    selectors: dict[str, list[str]],
    credentials: Credentials,
    *,
    manual_login: bool,
    headless: bool,
    no_wait: bool,
    allow_navigation: bool = True,
    article_title: str = "",
    allow_duplicate: bool = False,
) -> None:
    page.goto(url, wait_until="domcontentloaded")
    if "500 Internal Server Error" in page.title() and url.rstrip("/") != DASHBOARD_URL.rstrip("/"):
        page.goto(DASHBOARD_URL, wait_until="domcontentloaded")
    ensure_logged_in(
        page,
        credentials,
        manual_login=manual_login,
        headless=headless,
        no_wait=no_wait,
    )
    if is_login_page(page):
        raise SenkyoComError("ログイン後も編集画面へ到達できませんでした。")
    if page.url.rstrip("/") != url.rstrip("/"):
        page.goto(url, wait_until="domcontentloaded")
    if editor_fields_present(page, selectors):
        return
    if not allow_navigation:
        raise SenkyoComError(
            f"指定した記事の編集画面を開けませんでした: {url} "
            "URLが正しいか、記事が存在するかを確認してください。"
        )

    if article_title:
        reject_existing_post(page, article_title, allow_duplicate)

    new_post_url = find_new_post_url(page)
    if new_post_url is not None:
        page.goto(new_post_url, wait_until="domcontentloaded")
        page.wait_for_timeout(300)
        if editor_fields_present(page, selectors):
            return

    click_unique_named(page, SECTION_LINK_LABELS)
    if editor_fields_present(page, selectors):
        return

    if article_title:
        reject_existing_post(page, article_title, allow_duplicate)

    new_post_url = find_new_post_url(page)
    if new_post_url is not None:
        page.goto(new_post_url, wait_until="domcontentloaded")
        page.wait_for_timeout(300)
        if editor_fields_present(page, selectors):
            return

    click_unique_named(page, NEW_POST_LABELS)
    if editor_fields_present(page, selectors):
        return

    raise SenkyoComError(
        "投稿画面を自動検出できませんでした。inspect コマンドで管理画面を調査し、"
        "--editor-url または --selector-config を指定してください。"
    )


def set_title(page: Page, selectors: dict[str, list[str]], title: str) -> None:
    locator = visible_locator(page, selectors["title"]) or find_by_label_or_placeholder(
        page, ("タイトル", "記事タイトル", "見出し")
    )
    if locator is None:
        raise SenkyoComError("タイトル入力欄を検出できませんでした。")
    locator.fill(title)


def dispatch_editor_value(locator: Locator, content: str) -> None:
    locator.evaluate(
        """(element, value) => {
            if (window.tinymce && element.id && window.tinymce.get(element.id)) {
                window.tinymce.get(element.id).setContent(value);
                window.tinymce.get(element.id).save();
                return;
            }
            if (window.CKEDITOR && element.id && window.CKEDITOR.instances[element.id]) {
                window.CKEDITOR.instances[element.id].setData(value);
                window.CKEDITOR.instances[element.id].updateElement();
                return;
            }
            if (element.id) {
                const trix = document.querySelector(`trix-editor[input="${CSS.escape(element.id)}"]`);
                if (trix && trix.editor) {
                    trix.editor.loadHTML(value);
                    return;
                }
            }
            const tag = element.tagName.toLowerCase();
            if (tag === 'textarea' || tag === 'input') {
                element.value = value;
            } else {
                element.innerHTML = value;
            }
            element.dispatchEvent(new InputEvent('input', {bubbles: true, inputType: 'insertText', data: null}));
            element.dispatchEvent(new Event('change', {bubbles: true}));
        }""",
        content,
    )


def set_body(page: Page, selectors: dict[str, list[str]], body_html: str) -> None:
    ckeditor5_marker = page.locator("#post_is_created_ckeditor5[value='true'], .ck-editor")
    if ckeditor5_marker.count() > 0:
        source_buttons = page.get_by_role("button", name="ソース", exact=True)
        visible_source_buttons = [
            source_buttons.nth(index)
            for index in range(source_buttons.count())
            if source_buttons.nth(index).is_visible()
        ]
        if len(visible_source_buttons) == 1:
            # force=True: 直前の本文画像アップロードで残る「画像幅」等の
            # フローティングツールバーがボタンを一時的に覆うことがあるため。
            visible_source_buttons[0].click(force=True)
            source_area = page.locator(".ck-source-editing-area textarea, textarea.ck-source-editing-area")
            for index in range(source_area.count()):
                candidate = source_area.nth(index)
                if candidate.is_visible():
                    candidate.fill(body_html)
                    visible_source_buttons[0].click(force=True)
                    return

    locator = visible_locator(page, selectors["body"])
    if locator is not None:
        dispatch_editor_value(locator, body_html)
        return

    for frame in page.frames:
        if frame == page.main_frame:
            continue
        body = frame.locator("body[contenteditable='true'], body.mce-content-body, body.cke_editable")
        if body.count() > 0:
            dispatch_editor_value(body.nth(0), body_html)
            return

    locator = any_locator(page, selectors["body"])
    if locator is not None:
        dispatch_editor_value(locator, body_html)
        return
    raise SenkyoComError("本文エディターを検出できませんでした。")


def validate_image_path(image_path: Path) -> None:
    if not image_path.is_file():
        raise SenkyoComError(f"画像が見つかりません: {image_path}")
    if image_path.suffix.lower() not in SUPPORTED_IMAGE_SUFFIXES:
        raise SenkyoComError(f"対応していない画像形式です: {image_path.suffix}")
    if image_path.stat().st_size > MAX_IMAGE_BYTES:
        raise SenkyoComError("画像が13MBを超えています。選挙ドットコムの上限以下にしてください。")


def upload_image(page: Page, selectors: dict[str, list[str]], image_path: Path) -> None:
    validate_image_path(image_path)

    locator = any_locator(page, selectors["image"])
    if locator is None:
        click_unique_named(page, IMAGE_BUTTON_LABELS)
        locator = any_locator(page, selectors["image"])
    if locator is None:
        raise SenkyoComError("画像アップロード欄を検出できませんでした。")
    locator.set_input_files(str(image_path.resolve()))
    page.wait_for_timeout(500)


CONTENT_IMAGE_SRC_PATTERN = re.compile(r'<img src="([^"]+)"')


def find_local_content_image_srcs(html_body: str) -> list[str]:
    """本文HTML内の <img src> のうち、ローカルファイルパスのものを出現順・重複なしで返す。"""

    seen: dict[str, None] = {}
    for src in CONTENT_IMAGE_SRC_PATTERN.findall(html_body):
        unescaped = html.unescape(src)
        if re.match(r"^https?://", unescaped):
            continue
        seen.setdefault(unescaped, None)
    return list(seen)


def upload_content_image(page: Page, image_path: Path) -> str:
    """CKEditor5の「画像挿入」ボタン経由で本文用画像をアップロードし、公開URLを返す。"""

    validate_image_path(image_path)
    button = page.locator(CONTENT_IMAGE_BUTTON_SELECTOR)
    if button.count() == 0:
        raise SenkyoComError(
            "本文への画像挿入ボタン（画像挿入）を検出できませんでした。"
            "CKEditorのツールバー構成が変わった可能性があります。"
        )
    try:
        with page.expect_response(
            lambda response: CONTENT_IMAGE_UPLOAD_URL_FRAGMENT in response.url,
            timeout=30_000,
        ) as response_info:
            with page.expect_file_chooser(timeout=10_000) as chooser_info:
                button.click()
            chooser_info.value.set_files(str(image_path.resolve()))
    except PlaywrightTimeoutError as exc:
        raise SenkyoComError(f"本文画像のアップロードが完了しませんでした: {image_path}") from exc

    response = response_info.value
    try:
        payload = response.json()
    except Exception as exc:  # noqa: BLE001 - レスポンスがJSONでない場合の防御
        raise SenkyoComError(f"本文画像アップロードの応答を解析できませんでした: {image_path}") from exc

    url = payload.get("url") if isinstance(payload, dict) else None
    if not response.ok or not url:
        message = ""
        if isinstance(payload, dict):
            error = payload.get("error")
            if isinstance(error, dict):
                message = str(error.get("message") or "")
        raise SenkyoComError(f"本文画像のアップロードに失敗しました: {image_path} {message}".strip())

    page.wait_for_timeout(500)
    page.keyboard.press("Escape")
    page.wait_for_timeout(200)
    # アップロードでCKEditorがエディター本文へ自動挿入した画像を取り消す。
    # 実際の埋め込みはset_body()での本文HTML全体置換で行うため、
    # ここで挿入されたままだと本文が二重になったり、画像選択時の
    # フローティングツールバーが後続操作を妨げたりする。
    page.keyboard.press("Control+z")
    page.wait_for_timeout(300)
    return url


def embed_content_images(page: Page, html_body: str) -> str:
    """本文HTML中のローカル画像パスを、実際にアップロードした公開URLへ置き換える。"""

    local_srcs = find_local_content_image_srcs(html_body)
    updated = html_body
    for local_src in local_srcs:
        uploaded_url = upload_content_image(page, Path(local_src))
        updated = updated.replace(
            f'src="{html.escape(local_src, quote=True)}"',
            f'src="{html.escape(uploaded_url, quote=True)}"',
        )
    return updated


def save_draft(page: Page, button_text_override: str = "") -> None:
    labels = (button_text_override,) if button_text_override else DRAFT_BUTTON_LABELS
    for label in labels:
        if not label:
            continue
        if ("公開" in label or "投稿" in label) and "下書き" not in label:
            raise SenkyoComError(f"公開につながるボタン名は指定できません: {label}")
    for label in labels:
        candidates = [
            page.get_by_role("button", name=label, exact=True),
            page.locator(f"input[type='submit'][value='{label}']"),
        ]
        for locator in candidates:
            visible = [locator.nth(index) for index in range(locator.count()) if locator.nth(index).is_visible()]
            if visible:
                visible[0].click()
                return
    raise SenkyoComError(
        "「下書き保存」ボタンを検出できませんでした。"
        "実際のボタン名を --draft-button-text で指定してください。"
    )


def confirm_draft_saved(page: Page, editor_url: str) -> None:
    """保存後の成功メッセージまたは画面遷移を最大15秒待って確認する。

    保存が入力エラー（例:本文が空）で失敗した場合、フォームはURLを
    editor_url（…/posts/new）から送信先の…/postsへ変えたまま「新規作成」
    画面をエラーバナー付きで再表示することがある。この状態はURLだけ見ると
    「別画面へ遷移した」ように見えてしまうため、成功メッセージが無い状態で
    先にエラーバナーを検出し、誤って成功と判定しないようにする。
    """
    success_labels = ("作成しました", "保存しました", "更新しました")
    error_labels = ("エラーが発生しました",)
    for _ in range(30):
        for label in success_labels:
            try:
                if page.get_by_text(label, exact=False).count() > 0:
                    return
            except PlaywrightTimeoutError:
                continue
        for label in error_labels:
            try:
                error_banner = page.get_by_text(label, exact=False)
                if error_banner.count() > 0:
                    detail = ""
                    try:
                        detail = error_banner.first.locator("xpath=..").inner_text()
                    except PlaywrightTimeoutError:
                        pass
                    raise SenkyoComError(f"下書き保存が失敗しました: {detail or label}")
            except PlaywrightTimeoutError:
                continue
        if page.url.rstrip("/") != editor_url.rstrip("/") and "/new" not in page.url:
            return
        page.wait_for_timeout(500)
    raise SenkyoComError(
        "下書き保存の完了を確認できませんでした。"
        "保存後スクリーンショットとブラウザーの状態を確認してください。"
    )


def wait_for_user(message: str, enabled: bool) -> None:
    if not enabled:
        return
    if not sys.stdin.isatty():
        raise SenkyoComError("対話待ちが必要です。端末から実行するか --no-wait を指定してください。")
    input(f"{message}\n完了したら Enter を押してください: ")


def write_inspection(page: Page, output: Path) -> None:
    controls: list[dict[str, Any]] = page.locator(
        "input, textarea, select, [contenteditable='true'], iframe, button, a"
    ).evaluate_all(
        """elements => elements.map(element => ({
            tag: element.tagName.toLowerCase(),
            type: element.getAttribute('type'),
            id: element.id || null,
            name: element.getAttribute('name'),
            placeholder: element.getAttribute('placeholder'),
            accept: element.getAttribute('accept'),
            href: element.getAttribute('href'),
            ariaLabel: element.getAttribute('aria-label'),
            text: ['button', 'a'].includes(element.tagName.toLowerCase())
                ? (element.innerText || '').trim().slice(0, 120)
                : ''
        }))"""
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps({"url": page.url, "title": page.title(), "controls": controls}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def command_login(args: argparse.Namespace) -> int:
    credentials = credentials_for_args(args)
    session = start_browser(args)
    try:
        session.page.goto(args.login_url or credentials.cms_url, wait_until="domcontentloaded")
        ensure_logged_in(
            session.page,
            credentials,
            manual_login=args.manual_login,
            headless=args.headless,
            no_wait=args.no_wait,
        )
        print(f"ログイン状態をブラウザープロファイルへ保存しました: {Path(args.profile_dir).resolve()}")
        return 0
    finally:
        session.close()


def command_inspect(args: argparse.Namespace) -> int:
    credentials = credentials_for_args(args)
    session = start_browser(args)
    try:
        session.page.goto(args.url or credentials.cms_url, wait_until="domcontentloaded")
        ensure_logged_in(
            session.page,
            credentials,
            manual_login=args.manual_login,
            headless=args.headless,
            no_wait=args.no_wait,
        )
        wait_for_user("調査したい投稿画面までブラウザーで移動してください。", not args.no_wait)
        output = Path(args.output).resolve()
        write_inspection(session.page, output)
        print(f"入力欄情報を書き出しました（入力値・Cookieは含みません）: {output}")
        return 0
    finally:
        session.close()


def command_draft(args: argparse.Namespace, update_url: str | None = None) -> int:
    article = extract_article(Path(args.article), args.title)
    image_path = Path(args.image).resolve() if args.image else None
    if image_path is not None:
        validate_image_path(image_path)
    content_image_srcs = find_local_content_image_srcs(article.body_html)
    for local_src in content_image_srcs:
        validate_image_path(Path(local_src))
    selectors = load_selector_config(Path(args.selector_config) if args.selector_config else None)
    artifact_dir = Path(args.artifact_dir).resolve()
    artifact_dir.mkdir(parents=True, exist_ok=True)

    if args.dry_run:
        preview = artifact_dir / "article-preview.html"
        preview.write_text(
            "<!doctype html><meta charset='utf-8'><title>"
            + html.escape(article.title)
            + "</title><h1>"
            + html.escape(article.title)
            + "</h1>"
            + article.body_html,
            encoding="utf-8",
        )
        print(f"タイトル: {article.title}")
        print(f"本文HTML: {preview}")
        print(f"画像: {image_path or '指定なし'}")
        print(f"本文内画像: {len(content_image_srcs)}件" if content_image_srcs else "本文内画像: なし")
        print("dry-run のためブラウザー操作・アップロード・保存は行っていません。")
        return 0

    credentials = credentials_for_args(args)
    session = start_browser(args)
    try:
        navigate_to_editor(
            session.page,
            update_url or getattr(args, "editor_url", "") or credentials.cms_url,
            selectors,
            credentials,
            manual_login=args.manual_login,
            headless=args.headless,
            no_wait=args.no_wait,
            allow_navigation=update_url is None,
            article_title=article.title,
            allow_duplicate=bool(getattr(args, "allow_duplicate", False)),
        )
        set_title(session.page, selectors, article.title)
        body_html = embed_content_images(session.page, article.body_html)
        set_body(session.page, selectors, body_html)
        if image_path is not None:
            upload_image(session.page, selectors, image_path)

        screenshot = artifact_dir / "prepared-draft.png"
        session.page.screenshot(path=str(screenshot), full_page=True)
        print(f"下書き入力を準備しました: {session.page.url}")
        print(f"確認用スクリーンショット: {screenshot}")

        if args.save_draft:
            editor_url = session.page.url
            save_draft(session.page, args.draft_button_text)
            try:
                session.page.wait_for_load_state("networkidle", timeout=10_000)
            except PlaywrightTimeoutError:
                pass
            saved_screenshot = artifact_dir / "saved-draft.png"
            try:
                confirm_draft_saved(session.page, editor_url)
            finally:
                session.page.screenshot(path=str(saved_screenshot), full_page=True)
            print(f"下書き保存を確認しました: {session.page.url}")
            print(f"保存後スクリーンショット: {saved_screenshot}")
        else:
            print("保存はしていません。保存する場合は内容確認後に --save-draft を付けて再実行してください。")

        wait_for_user("ブラウザー上の内容を確認してください。", not args.no_wait)
        return 0
    finally:
        session.close()


def command_update(args: argparse.Namespace) -> int:
    return command_draft(args, update_url=validate_post_edit_url(args.post_url))


def add_browser_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--profile-dir", default=str(DEFAULT_PROFILE_DIR), help="ログイン状態を保持する作業ディレクトリ")
    parser.add_argument("--secret", default=str(DEFAULT_SECRET_PATH), help="go2senkyo 認証情報を含むJSON（既定: secret.json）")
    parser.add_argument("--manual-login", action="store_true", help="secret.json を使わずブラウザーで手動ログインする")
    parser.add_argument("--channel", default="msedge", help="Playwright のブラウザーチャンネル（既定: msedge）")
    parser.add_argument("--headless", action="store_true", help="ブラウザーを非表示で実行（手動ログイン時は使用不可）")
    parser.add_argument("--timeout-ms", type=int, default=20_000, help="画面操作のタイムアウト（ミリ秒）")
    parser.add_argument("--no-wait", action="store_true", help="確認の Enter 入力を待たずにブラウザーを閉じる")


def add_article_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--article", required=True, help="先頭のH1をタイトルとするMarkdownファイル")
    parser.add_argument("--title", default="", help="MarkdownのH1より優先するタイトル")
    parser.add_argument("--image", default="", help="アップロードする13MB以下のJPEG/PNG/GIF画像")
    parser.add_argument("--selector-config", default="", help="入力欄CSSセレクターを上書きするJSON")
    parser.add_argument("--artifact-dir", default=str(DEFAULT_ARTIFACT_DIR), help="確認画像・HTMLの出力先")
    parser.add_argument("--save-draft", action="store_true", help="『下書き保存』ボタンを押す")
    parser.add_argument("--draft-button-text", default="", help="実画面の下書き保存ボタン名を明示する")
    parser.add_argument("--dry-run", action="store_true", help="HTML変換だけ行い、サイトへ接続しない")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="選挙ドットコムへブログ下書きを準備する")
    subparsers = parser.add_subparsers(dest="command", required=True)

    login = subparsers.add_parser("login", help="自動ログインを確認してセッションを保存する")
    add_browser_arguments(login)
    login.add_argument("--login-url", default="", help="省略時は secret.json の go2senkyo.cmsUrl")
    login.set_defaults(handler=command_login)

    inspect = subparsers.add_parser("inspect", help="ログイン後画面の入力欄情報を安全に書き出す")
    add_browser_arguments(inspect)
    inspect.add_argument("--url", default="", help="省略時は secret.json の go2senkyo.cmsUrl")
    inspect.add_argument("--output", default="work/senkyo-com/artifacts/form-controls.json")
    inspect.set_defaults(handler=command_inspect)

    draft = subparsers.add_parser("draft", help="Markdownと画像を新規投稿画面へ入力する")
    add_browser_arguments(draft)
    add_article_arguments(draft)
    draft.add_argument("--editor-url", default="", help="省略時は secret.json の go2senkyo.cmsUrl")
    draft.add_argument(
        "--allow-duplicate",
        action="store_true",
        help="同じタイトルの記事が見つかっても新規作成する（通常は指定しない）",
    )
    draft.set_defaults(handler=command_draft)

    update = subparsers.add_parser("update", help="既存記事の編集画面へMarkdownと画像を入力する")
    add_browser_arguments(update)
    add_article_arguments(update)
    update.add_argument(
        "--post-url",
        required=True,
        help="更新する記事の編集URL（…/posts/<記事ID>/edit）",
    )
    update.set_defaults(handler=command_update)
    return parser


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(errors="replace")
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.handler(args))
    except (SenkyoComError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    except PlaywrightError as exc:
        print(f"ERROR: ブラウザー操作に失敗しました: {exc}", file=sys.stderr)
        return 3
    except KeyboardInterrupt:
        print("中断しました。", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
