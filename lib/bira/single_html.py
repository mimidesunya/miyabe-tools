# -*- coding: utf-8 -*-
r"""差し替え済みHTML → 単一ファイルHTML（CSS・画像・フォントを全部埋め込む）

    python single_html.py <入力HTML> <出力HTML>

I:\...\ビラ\埋め込み.py をサーバー用に移植したもの。出力を変えないため、
サブセットの条件と font-family の置換規則はそのまま踏襲している。
フォントは同じフォルダの NotoSansJP-VF.ttf を使う（Windows のパスは見ない）。
"""
import base64, io, mimetypes, os, re, sys, urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
FONT = os.path.join(HERE, "NotoSansJP-VF.ttf")


def data_uri(path):
    mime = mimetypes.guess_type(path)[0] or "application/octet-stream"
    if path.lower().endswith(".svg"):
        mime = "image/svg+xml"
    return "data:%s;base64,%s" % (mime, base64.b64encode(open(path, "rb").read()).decode())


def inline_css(html, root):
    def rep(m):
        p = os.path.join(root, urllib.parse.unquote(m.group(1)))
        if not os.path.exists(p):
            return m.group(0)
        return "<style>\n%s\n</style>" % io.open(p, encoding="utf-8").read()
    return re.sub(r'<link[^>]*rel="stylesheet"[^>]*href="([^"]+)"[^>]*>', rep, html)


def inline_images(html, root):
    def rep(m):
        head, url, tail = m.group(1), m.group(2), m.group(3)
        if url.startswith(("data:", "http:", "https:")):
            return m.group(0)
        p = os.path.join(root, urllib.parse.unquote(url))
        if not os.path.exists(p):
            return m.group(0)
        return head + data_uri(p) + tail
    return re.sub(r'(src=")([^"]+)(")', rep, html)


def used_chars(html):
    """タグとstyleを除いた本文の文字。サブセットの対象。"""
    t = re.sub(r"<style.*?</style>|<script.*?</script>", " ", html, flags=re.S)
    t = re.sub(r"<[^>]+>", " ", t)
    import html as H
    t = H.unescape(t)
    base = ("0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
            " 　、。「」（）〜・：／，．-–—→＠％")
    return set(t) | set(base)


def subset_woff2(chars):
    from fontTools import subset
    from fontTools.ttLib import TTFont
    font = TTFont(FONT)
    opt = subset.Options()
    opt.layout_features = ["*"]
    opt.name_IDs = ["*"]
    opt.notdef_outline = True
    opt.recalc_bounds = True
    opt.drop_tables = []
    sub = subset.Subsetter(options=opt)
    sub.populate(text="".join(sorted(chars)))
    sub.subset(font)
    font.flavor = "woff2"
    buf = io.BytesIO()
    font.save(buf)
    return buf.getvalue()


FACE = """
/* ===== 埋め込みフォント（Noto Sans JP 可変・本文で使う文字のみ） ===== */
@font-face {
  font-family: "Noto Sans JP";
  src: url(data:font/woff2;base64,%s) format("woff2");
  font-weight: 100 900;
  font-style: normal;
  font-display: block;
}
"""


def convert(src, dst):
    root = os.path.dirname(os.path.abspath(src))
    html = io.open(src, encoding="utf-8").read()
    html = inline_css(html, root)
    html = inline_images(html, root)

    face = FACE % base64.b64encode(subset_woff2(used_chars(html))).decode()

    # Copper 用に family 名で指定していた中間ウエイトは、可変フォントでは
    # font-weight で表せる。埋め込み版では weight 指定へ置き換える。
    html = re.sub(r'font-family:\s*"Noto Sans JP Medium",\s*"Noto Sans JP",\s*sans-serif;\s*\n(\s*)font-weight:\s*400;',
                  r'font-family: "Noto Sans JP", sans-serif;\n\1font-weight: 500;', html)
    html = html.replace('"Noto Sans JP Medium", "Noto Sans JP", sans-serif', '"Noto Sans JP", sans-serif')

    html = html.replace("<style>", "<style>" + face, 1)
    io.open(dst, "w", encoding="utf-8", newline="\n").write(html)


if __name__ == "__main__":
    convert(sys.argv[1], sys.argv[2])
