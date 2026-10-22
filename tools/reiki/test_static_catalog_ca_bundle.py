"""取得元が送ってこない中間証明書を、certifi の束に足して検証する。

西宮市（regulations.bk2reiki.net）は 2026-10-09 の証明書の更新から、発行元と違う
古い中間証明書を送っている。本番の requests は検証に落ち、例規を取り直せていなかった。
"""

import re
import ssl
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent / "scrapers"))

import certifi  # noqa: E402
import static_catalog  # noqa: E402

_PEM_RE = re.compile(r"-----BEGIN CERTIFICATE-----.+?-----END CERTIFICATE-----", re.S)


class CaBundleTest(unittest.TestCase):
    def test_extra_intermediate_is_the_globalsign_issuer_of_nishinomiya(self):
        text = static_catalog.EXTRA_INTERMEDIATES.read_text(encoding="utf-8")
        pems = _PEM_RE.findall(text)
        self.assertEqual(len(pems), 1)
        der = ssl.PEM_cert_to_DER_cert(pems[0])
        # 西宮市の証明書の Authority Key Identifier と一致する Subject Key Identifier
        self.assertIn(bytes.fromhex("0869E97FA5ABF4C361E5CD8E7B47EF8B29648DEC"), der)

    def test_bundle_adds_intermediate_to_certifi_and_loads(self):
        path = static_catalog.ca_bundle_path()
        self.assertIsInstance(path, str)
        bundle = Path(path).read_text(encoding="utf-8")
        base = Path(certifi.where()).read_text(encoding="utf-8")
        self.assertEqual(len(_PEM_RE.findall(bundle)), len(_PEM_RE.findall(base)) + 1)
        context = ssl.create_default_context(cafile=path)
        subjects = [dict(item[0] for item in cert["subject"]).get("commonName") for cert in context.get_ca_certs()]
        self.assertIn("GlobalSign GCC R46 DV TLS CA 2025", subjects)
        # 2 回目は同じ束を使い回す
        self.assertEqual(static_catalog.ca_bundle_path(), path)

    def test_without_extra_file_falls_back_to_requests_default(self):
        with mock.patch.object(static_catalog, "EXTRA_INTERMEDIATES", Path(__file__).with_name("no-such.pem")):
            self.assertIs(static_catalog.ca_bundle_path(), True)


if __name__ == "__main__":
    unittest.main()
