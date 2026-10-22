"""RTF の本文を取り出すことの確認（菊陽町の会議の文書は Word から書き出した RTF）。"""

import unittest

from tools.gijiroku import rtf_text


def esc(text: str, encoding: str = "cp932") -> str:
    """文字列を RTF の `\\'xx` の並びにする。Word は日本語をこの形で書き出す。"""
    return "".join(f"\\'{byte:02x}" for byte in text.encode(encoding))


def uni(code: int) -> str:
    """RTF の Unicode 1 文字の書き方（`u` と 10 進の値）。"""
    return "\\" + "u" + str(code)


def rtf(body: str, header: str = r"\ansi\ansicpg932\uc2\deff0") -> bytes:
    return ("{\\rtf1" + header + body + "}").encode("latin-1")


class LooksLikeRtfTest(unittest.TestCase):
    def test_magic(self) -> None:
        self.assertTrue(rtf_text.looks_like_rtf(b"{\\rtf1\\ansi x}"))
        self.assertTrue(rtf_text.looks_like_rtf(b"\xef\xbb\xbf\r\n {\\rtf1 x}"))
        self.assertFalse(rtf_text.looks_like_rtf(b"%PDF-1.7"))
        self.assertFalse(rtf_text.looks_like_rtf(b"<html></html>"))
        self.assertFalse(rtf_text.looks_like_rtf(b""))

    def test_not_rtf_gives_empty_text(self) -> None:
        self.assertEqual(rtf_text.extract_rtf_text(b"%PDF-1.7 body"), "")


class ShiftJisEscapeTest(unittest.TestCase):
    def test_double_byte_escapes_are_joined(self) -> None:
        body = r"{\fonttbl{\f0\froman\fcharset0 Times New Roman;}{\f15\fmodern\fcharset128 " + esc("ＭＳ ゴシック") + ";}}"
        body += r"\pard\plain {\loch\af15\hich\af15\dbch\f15 " + esc("令和４年第２回菊陽町議会臨時会") + "}\\par "
        self.assertEqual(rtf_text.extract_rtf_text(rtf(body)), "令和４年第２回菊陽町議会臨時会")

    def test_pair_split_across_groups(self) -> None:
        # 書式の切れ目で 2 バイト文字の 1 バイト目と 2 バイト目が別の組に入っても読む。
        raw = esc("令")
        body = r"{\fonttbl{\f1\fcharset128 MS Mincho;}}{\f1 " + raw[:4] + "}{\\f1 " + raw[4:] + "}"
        self.assertEqual(rtf_text.extract_rtf_text(rtf(body)), "令")

    def test_cp932_only_characters(self) -> None:
        # 「﨑」「①」は Shift_JIS になく CP932 にだけある。
        self.assertEqual(rtf_text.extract_rtf_text(rtf(esc("川﨑①"))), "川﨑①")

    def test_charset_from_font_table_without_ansicpg(self) -> None:
        # `\ansicpg` を書かない古い書き出しでも、Shift_JIS のフォントがあれば日本語として読む。
        body = r"{\fonttbl{\f0\fcharset128 MS Mincho;}}\f0 " + esc("会議録")
        self.assertEqual(rtf_text.extract_rtf_text(rtf(body, header=r"\ansi\deff0")), "会議録")

    def test_raw_eight_bit_bytes(self) -> None:
        # エスケープせずにバイトをそのまま置く書き出し。「表」の 2 バイト目 0x5C は `\\` になる。
        raw = b"{\\rtf1\\ansi\\ansicpg932 " + "会議".encode("cp932") + b"\x95\\\\}"
        self.assertEqual(rtf_text.extract_rtf_text(raw), "会議表")


class UnicodeEscapeTest(unittest.TestCase):
    def test_fallback_after_unicode_is_skipped(self) -> None:
        # \uc2 なら、続く `\'xx` 2 つは代わりの文字なので読まない。
        body = uni(20250) + esc("会") + uni(35696) + esc("議")
        self.assertEqual(rtf_text.extract_rtf_text(rtf(body)), "会議")

    def test_uc1_question_mark(self) -> None:
        body = r"\uc1" + uni(20250) + "?" + uni(37682) + "?"
        self.assertEqual(rtf_text.extract_rtf_text(rtf(body)), "会録")

    def test_negative_value(self) -> None:
        # 32767 を超える文字は負の数で書かれる（ｱ = U+FF71 = 65393 = -143）。
        self.assertEqual(rtf_text.extract_rtf_text(rtf(r"\uc1\u-143?")), "ｱ")

    def test_surrogate_pair(self) -> None:
        # 𠮷（U+20BB7）は `\u` 2 つのサロゲートの組で書かれる。
        self.assertEqual(rtf_text.extract_rtf_text(rtf(r"\uc1\u-10174?\u-8265?")), "𠮷")

    def test_uc_is_scoped_to_the_group(self) -> None:
        # 組の中の \uc0 は代わりの文字を持たないので、続く「録」は本文。組を出ると
        # \uc2 に戻り、「議」の代わりの 2 バイトを読み飛ばす。
        body = r"{\uc0" + uni(20250) + esc("録") + "}" + uni(35696) + esc("議")
        self.assertEqual(rtf_text.extract_rtf_text(rtf(body)), "会録議")


class StructureTest(unittest.TestCase):
    def test_destinations_are_not_body(self) -> None:
        body = (
            r"{\fonttbl{\f0 Century;}}{\colortbl;\red0\green0\blue0;}"
            r"{\stylesheet{\s0 Normal;}}{\info{\title " + esc("会議結果") + r"}{\author " + esc("議会事務局") + "}}"
            r"{\*\generator Microsoft Word 11;}{\headerr {\pard 1 " + esc("ページ") + r"\par}}"
            r"{\field{\*\fldinst HYPERLINK \"https://example.jp\"}{\fldrslt " + esc("リンク") + "}}"
            r"\pard " + esc("会議録")
        )
        self.assertEqual(rtf_text.extract_rtf_text(rtf(body)), "リンク会議録")

    def test_paragraphs_and_tables(self) -> None:
        body = (
            r"\pard " + esc("議案番号") + r"\cell " + esc("結果") + r"\cell\row "
            + esc("議案第38号") + r"\cell " + esc("原案可決") + r"\cell\row\pard " + esc("以上") + r"\par"
        )
        self.assertEqual(
            rtf_text.extract_rtf_text(rtf(body)),
            "議案番号\t結果\n議案第38号\t原案可決\n以上",
        )

    def test_escaped_symbols(self) -> None:
        self.assertEqual(rtf_text.extract_rtf_text(rtf(r"a\{b\}c\\d\~e\emdash f")), "a{b}c\\d e—f")

    def test_binary_data_is_skipped(self) -> None:
        # `\bin3` の後の 3 バイト（ここでは `}}}`）は本文でも組の終わりでもない。
        raw = b"{\\rtf1\\ansi before{\\pict\\bin3 }}}}after}"
        self.assertEqual(rtf_text.extract_rtf_text(raw), "beforeafter")


class WordExportShapeTest(unittest.TestCase):
    """菊陽町の会議結果（Word 2003 以降の書き出し）と同じ形で、中身を読めるか。"""

    def test_kikuyo_meeting_result_shape(self) -> None:
        header = (
            r"\adeflang1025\ansi\ansicpg932\uc2\adeff0\deff0\stshfdbch11\stshfloch21"
            r"{\fonttbl{\f0\fbidi \froman\fcharset0\fprq2{\*\panose 02020603050405020304}Times New Roman;}"
            r"{\f15\fbidi \fmodern\fcharset128\fprq1{\*\panose 020b0609070205080204}" + esc("ＭＳ ゴシック")
            + r"{\*\falt MS Gothic};}}"
            r"{\colortbl;\red0\green0\blue0;}{\*\themedata 504b030414000600080000002100e9de0fbfff}"
            r"{\info{\title " + esc("会議結果") + r"}{\author kikuyo}}"
        )
        line1 = "令和４年第２回菊陽町議会臨時会"
        line2 = "１　開　　会　　　令和４年８月２３日"
        body = (
            r"\pard\plain \ltrpar\qc \rtlch\fcs1 \af0\afs21 \ltrch\fcs0 \fs21\lang1033\langfe1041"
            r"{\rtlch\fcs1 \af15\afs28 \ltrch\fcs0 \fs28\loch\af15\hich\af15\dbch\af15\insrsid1 "
            r"\loch\af15\hich\af15\dbch\f15 " + esc(line1[:2]) + "}"
            r"{\rtlch\fcs1 \af15\afs28 \ltrch\fcs0 \fs28\loch\af15\hich\af15\dbch\af15\insrsid2 "
            r"\loch\af15\hich\af15\dbch\f15 " + esc(line1[2:]) + "}"
            "\r\n\\par \\pard "
            r"{\loch\af15\hich\af15\dbch\f15 " + esc(line2) + "\\par }"
        )
        text = rtf_text.extract_rtf_text(rtf(body, header=header))
        self.assertEqual(text, f"{line1}\n{line2}")


if __name__ == "__main__":
    unittest.main()
