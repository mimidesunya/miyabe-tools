"""取得元台帳が、自分で言っていることと矛盾していないか。

`crawl_status=enabled` なのに URL が空、という行は、巡回からも探索ツールからも
見えない。例規で 32 件（岡崎市を含む）がこの形で放置され、公開画面では
「取得元 URL が未特定」に数えられているのに、台帳の上では取得対象のふりを
していた。取りこぼしとして見えるように、台帳の側で禁止する。
"""

import csv
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REGISTRIES = {
    "会議録": ROOT / "data" / "municipalities" / "assembly_minutes_system_urls.tsv",
    "例規": ROOT / "data" / "municipalities" / "reiki_system_urls.tsv",
}
VALID_STATUSES = {"enabled", "excluded", "unresolved", "review_required"}


def read_rows(path: Path) -> list[dict]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))


class RegistryConsistencyTest(unittest.TestCase):
    def test_enabled_rows_have_a_source_url(self):
        for label, path in REGISTRIES.items():
            offenders = [
                row["jis_code"]
                for row in read_rows(path)
                if (row.get("crawl_status") or "").strip() == "enabled"
                and (row.get("url") or "").strip() == ""
            ]
            self.assertEqual(
                offenders,
                [],
                f"{label}: URL が空のまま enabled になっている行がある: {offenders[:10]}",
            )

    def test_excluded_rows_say_why(self):
        for label, path in REGISTRIES.items():
            offenders = [
                row["jis_code"]
                for row in read_rows(path)
                if (row.get("crawl_status") or "").strip() == "excluded"
                and (row.get("exclusion_reason") or "").strip() == ""
            ]
            self.assertEqual(
                offenders, [], f"{label}: 理由の無い excluded がある: {offenders[:10]}"
            )

    def test_crawl_status_is_a_known_value(self):
        for label, path in REGISTRIES.items():
            unknown = sorted(
                {
                    (row.get("crawl_status") or "").strip()
                    for row in read_rows(path)
                    if (row.get("crawl_status") or "").strip() not in VALID_STATUSES
                }
            )
            self.assertEqual(unknown, [], f"{label}: 知らない crawl_status: {unknown}")

    def test_no_byte_order_mark(self):
        """BOM が付くと PHP 側で jis_code を引けず、公開画面の件数が全部 0 になる。"""
        for label, path in REGISTRIES.items():
            head = path.read_bytes()[:3]
            self.assertNotEqual(
                head, b"\xef\xbb\xbf", f"{label}: 先頭に BOM が付いている"
            )


if __name__ == "__main__":
    unittest.main()
