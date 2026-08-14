from __future__ import annotations

import unittest
from pathlib import Path


class DomainDependencyTest(unittest.TestCase):
    def test_shared_municipality_catalog_does_not_build_poster_board_features(self) -> None:
        repository_root = Path(__file__).resolve().parents[3]
        shared_catalog = (repository_root / "lib" / "municipalities.php").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("election_poster_boards", shared_catalog)
        self.assertNotIn("poster_boards_", shared_catalog)
        self.assertIn("array_key_exists('boards', $entry)", shared_catalog)

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

    def test_deploy_protects_sqlite_sidecars(self) -> None:
        repository_root = Path(__file__).resolve().parents[3]
        deploy_source = (repository_root / "deploy" / "deploy.py").read_text(
            encoding="utf-8"
        )
        for database_name in ("boards.sqlite", "tasks.sqlite", "users.sqlite"):
            self.assertIn(f'"exclude:{database_name}*"', deploy_source)


if __name__ == "__main__":
    unittest.main()
