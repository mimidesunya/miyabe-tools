from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from post_draft import (
    SenkyoComError,
    extract_article,
    load_credentials,
    markdown_to_html,
    normalize_new_post_url,
    validate_image_path,
    validate_post_edit_url,
)


class MarkdownConversionTest(unittest.TestCase):
    def test_supported_markdown_is_converted(self) -> None:
        converted = markdown_to_html(
            "## 見出し\n\n- 項目A\n- 項目B\n\n**重要**です。[資料](https://example.com/doc)"
        )
        self.assertIn("<h2>見出し</h2>", converted)
        self.assertIn("<ul><li>項目A</li><li>項目B</li></ul>", converted)
        self.assertIn("<strong>重要</strong>", converted)
        self.assertIn('href="https://example.com/doc"', converted)

    def test_html_is_escaped(self) -> None:
        converted = markdown_to_html("<script>alert(1)</script>")
        self.assertNotIn("<script>", converted)
        self.assertIn("&lt;script&gt;", converted)


class ArticleExtractionTest(unittest.TestCase):
    def test_first_h1_becomes_title(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "article.md"
            path.write_text("# 記事タイトル\n\n本文です。", encoding="utf-8")
            article = extract_article(path)
        self.assertEqual("記事タイトル", article.title)
        self.assertEqual("本文です。", article.body_markdown)

    def test_title_is_required(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "article.md"
            path.write_text("本文だけです。", encoding="utf-8")
            with self.assertRaises(SenkyoComError):
                extract_article(path)


class ImageValidationTest(unittest.TestCase):
    def test_jpeg_is_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "photo.jpg"
            path.write_bytes(b"test")
            validate_image_path(path)

    def test_missing_image_is_rejected(self) -> None:
        with self.assertRaises(SenkyoComError):
            validate_image_path(Path("missing-photo.jpg"))

    def test_gif_is_accepted_and_webp_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            gif = Path(directory) / "photo.gif"
            gif.write_bytes(b"GIF89a")
            validate_image_path(gif)

            webp = Path(directory) / "photo.webp"
            webp.write_bytes(b"RIFF")
            with self.assertRaises(SenkyoComError):
                validate_image_path(webp)

    def test_image_over_13mb_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "large.jpg"
            with path.open("wb") as stream:
                stream.truncate(13 * 1024 * 1024 + 1)
            with self.assertRaises(SenkyoComError):
                validate_image_path(path)


class CredentialsTest(unittest.TestCase):
    def test_go2senkyo_credentials_are_loaded(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "secret.json"
            path.write_text(
                '{"go2senkyo":{"cmsUrl":"https://www.go2senkyo.com/cms","id":"user","password":"pass"}}',
                encoding="utf-8",
            )
            credentials = load_credentials(path)
        self.assertEqual("https://www.go2senkyo.com/cms", credentials.cms_url)
        self.assertEqual("user", credentials.login_id)
        self.assertEqual("pass", credentials.password)

    def test_external_cms_host_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "secret.json"
            path.write_text(
                '{"go2senkyo":{"cmsUrl":"https://example.com/cms","id":"user","password":"pass"}}',
                encoding="utf-8",
            )
            with self.assertRaises(SenkyoComError):
                load_credentials(path)


class PostEditUrlTest(unittest.TestCase):
    def test_edit_url_is_accepted(self) -> None:
        url = "https://www.go2senkyo.com/cms/politicians/6880/posts/1367869-/edit"
        self.assertEqual(url, validate_post_edit_url(url))

    def test_surrounding_whitespace_is_stripped(self) -> None:
        url = "https://www.go2senkyo.com/cms/politicians/6880/posts/1367869-/edit"
        self.assertEqual(url, validate_post_edit_url(f"  {url}\n"))

    def test_non_edit_url_is_rejected(self) -> None:
        for url in (
            "https://www.go2senkyo.com/cms/politicians/6880/posts/new",
            "https://www.go2senkyo.com/cms/politicians/6880/posts",
            "https://example.com/cms/politicians/6880/posts/1367869-/edit",
            "https://www.go2senkyo.com/cms/politicians/6880/posts/1367869-/edit?next=/x",
        ):
            with self.assertRaises(SenkyoComError, msg=url):
                validate_post_edit_url(url)


class NewPostUrlTest(unittest.TestCase):
    def test_relative_new_post_url_is_normalized(self) -> None:
        self.assertEqual(
            "https://www.go2senkyo.com/cms/politicians/6880/posts/new",
            normalize_new_post_url(
                "https://www.go2senkyo.com/cms",
                "/cms/politicians/6880/posts/new",
            ),
        )

    def test_external_or_non_new_url_is_rejected(self) -> None:
        for href in (
            "https://example.com/cms/politicians/6880/posts/new",
            "/cms/politicians/6880/posts",
            "/cms/politicians/6880/posts/123/edit",
        ):
            with self.assertRaises(SenkyoComError, msg=href):
                normalize_new_post_url("https://www.go2senkyo.com/cms", href)


if __name__ == "__main__":
    unittest.main()
