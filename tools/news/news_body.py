"""お知らせのリンク先のページから、本文と添付の一覧を抜き出す。

自治体の CMS はまちまちなので、決め打ちの場所と、本文の集まる所をたどる方法を
組み合わせる（2026-11-14 に全国 300 件で試した。100 字未満は 2 件）。

1. 見出し・メニュー・パンくず・フッターなど、本文でない所を外す。
2. よくある本文の枠（`article`・`main`・`#main`・`#tmp_contents` など）を順に探し、
   100 字以上あればそれを使う（300 件中 268 件）。
3. 無ければ `body` から、リンクでない文字の 7 割以上を持つ子へ降りていく
   （`.mol_contents`・`#contentBody`・`#ContentPane` などに行き着く）。

添付（PDF・Word・Excel など）は URL とリンクの文字だけ控える。お知らせの半分は
添付があり、中身はまだ読まない。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlsplit

from lxml import html as lxml_html

TEXT_MAX_CHARS = 30_000
MIN_MAIN_CHARS = 100
# 子へ降りるのは、リンクでない文字の 7 割以上がその子にあるとき。
DESCEND_SHARE = 0.7
DESCEND_MIN_CHARS = 200
MAX_ATTACHMENTS = 50
ATTACHMENT_EXTENSIONS = (
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".xlsm", ".ppt", ".pptx", ".csv", ".zip", ".odt", ".ods", ".jtd",
)
MAIN_SELECTORS = (
    "article", "main", "#main", "#tmp_contents", "#contents", "#content", ".contents", "#main_body", "#mainContents",
)
DROP_XPATH = (
    "//script|//style|//noscript|//template|//nav|//header|//footer|//aside|//select"
    "|//*[@id='breadcrumb' or @id='pankuzu' or @id='tmp_pankuzu' or @id='sidebar' or @id='side'"
    " or @id='tmp_wrap_navigation']"
    "|//*[contains(@class, 'breadcrumb') or contains(@class, 'pankuzu')]"
)
BLOCK_TAGS = {
    "p", "div", "section", "article", "main", "li", "ul", "ol", "dl", "dt", "dd", "tr", "table", "thead", "tbody",
    "h1", "h2", "h3", "h4", "h5", "h6", "blockquote", "pre", "figure", "figcaption", "caption", "address", "hr",
}
CHARSET_ALIASES = {
    "shift_jis": "cp932", "shift-jis": "cp932", "sjis": "cp932", "x-sjis": "cp932", "windows-31j": "cp932",
    "euc-jp": "euc_jp", "x-euc-jp": "euc_jp",
}
CONTENT_TYPE_CHARSET_RE = re.compile(r"charset=[\"']?([A-Za-z0-9_\-]+)", re.I)
META_CHARSET_RE = re.compile(rb"<meta[^>]+charset=[\"']?([A-Za-z0-9_\-]+)", re.I)
XML_DECLARATION_RE = re.compile(r"^\s*<\?xml[^>]*\?>")
SPACES_RE = re.compile(r"[ \t 　​]+")


@dataclass
class PageBody:
    title: str
    text: str
    method: str
    attachments: list[dict[str, str]] = field(default_factory=list)


def decode_html(raw: bytes, content_type: str = "") -> str:
    """応答の Content-Type、meta の charset、UTF-8、Shift_JIS、EUC-JP の順に試す。"""
    candidates = []
    found = CONTENT_TYPE_CHARSET_RE.search(content_type or "")
    if found:
        candidates.append(found.group(1))
    found = META_CHARSET_RE.search(raw[:4096])
    if found:
        candidates.append(found.group(1).decode("ascii", "replace"))
    candidates += ["utf-8", "cp932", "euc_jp"]
    for name in candidates:
        codec = CHARSET_ALIASES.get(name.lower(), name)
        try:
            return raw.decode(codec)
        except (LookupError, UnicodeDecodeError):
            continue
    return raw.decode("utf-8", "replace")


def _collapse(value: str) -> str:
    return SPACES_RE.sub(" ", value).strip()


def _plain_length(node) -> int:
    """リンクでない文字の数（メニューや一覧の枠に降りないため）。"""
    total = len(_collapse(node.text_content()))
    links = sum(len(_collapse(anchor.text_content())) for anchor in node.iter("a"))
    return total - links


def _find(doc, selector: str) -> list:
    if selector.startswith("#"):
        return doc.xpath(f"//*[@id='{selector[1:]}']")
    if selector.startswith("."):
        return doc.xpath(f"//*[contains(concat(' ', normalize-space(@class), ' '), ' {selector[1:]} ')]")
    return doc.xpath(f"//{selector}")


def _descend(node):
    while True:
        here = _plain_length(node)
        if here < DESCEND_MIN_CHARS:
            return node
        child = next((child for child in node if isinstance(child.tag, str)
                      and _plain_length(child) >= DESCEND_SHARE * here), None)
        if child is None:
            return node
        node = child


def node_text(node) -> str:
    """段落ごとに改行を入れた本文。"""
    for element in node.iter():
        if not isinstance(element.tag, str):
            continue
        tag = element.tag.lower()
        if tag == "br" or tag in BLOCK_TAGS:
            element.tail = "\n" + (element.tail or "")
        elif tag in ("td", "th"):
            element.tail = " " + (element.tail or "")
    lines = (_collapse(line) for line in node.text_content().splitlines())
    text = "\n".join(line for line in lines if line)
    return text[:TEXT_MAX_CHARS]


def _attachments(node, base_url: str) -> list[dict[str, str]]:
    found: dict[str, dict[str, str]] = {}
    for anchor in node.iter("a"):
        href = (anchor.get("href") or "").strip()
        if not href or href.startswith(("javascript:", "mailto:", "#")):
            continue
        url = urljoin(base_url, href)
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https") or not parts.path.lower().endswith(ATTACHMENT_EXTENSIONS):
            continue
        if url not in found:
            found[url] = {"url": url, "text": _collapse(anchor.text_content())[:200]}
        if len(found) >= MAX_ATTACHMENTS:
            break
    return list(found.values())


def extract_page(raw: bytes, content_type: str = "", base_url: str = "") -> PageBody:
    """HTML から本文を抜き出す。HTML として読めなければ ValueError。"""
    text = XML_DECLARATION_RE.sub("", decode_html(raw, content_type))
    if not text.strip():
        raise ValueError("中身が空です")
    doc = lxml_html.document_fromstring(text)
    title = _collapse(doc.findtext(".//title") or "")[:300]
    for bad in doc.xpath(DROP_XPATH):
        parent = bad.getparent()
        if parent is not None:
            parent.remove(bad)
    for selector in MAIN_SELECTORS:
        found = _find(doc, selector)
        if not found:
            continue
        best = max(found, key=lambda node: len(_collapse(node.text_content())))
        if len(_collapse(best.text_content())) >= MIN_MAIN_CHARS:
            return PageBody(title, node_text(best), selector, _attachments(best, base_url))
    body = doc.find("body")
    if body is None:
        return PageBody(title, "", "none")
    node = _descend(body)
    return PageBody(title, node_text(node), "descend" if node is not body else "body", _attachments(node, base_url))
