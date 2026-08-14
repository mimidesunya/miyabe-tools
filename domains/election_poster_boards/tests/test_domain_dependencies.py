from __future__ import annotations

import unittest
from pathlib import Path


class DomainDependencyTest(unittest.TestCase):
    def test_runtime_does_not_depend_on_municipal_document_services(self) -> None:
        domain_root = Path(__file__).resolve().parents[1]
        code_files = [
            path
            for path in domain_root.rglob("*")
            if path.is_file()
            and path.suffix in {".php", ".py"}
            and "tests" not in path.parts
        ]
        forbidden = {
            "opensearch": "OpenSearch",
            "/api/search": "document search API",
            "/api/document": "document detail API",
            "tools/gijiroku": "minutes crawler",
            "tools/reiki": "ordinance crawler",
        }

        for path in code_files:
            content = path.read_text(encoding="utf-8").lower()
            for marker, dependency in forbidden.items():
                self.assertNotIn(
                    marker,
                    content,
                    f"{path.relative_to(domain_root)} depends on {dependency}",
                )


if __name__ == "__main__":
    unittest.main()
