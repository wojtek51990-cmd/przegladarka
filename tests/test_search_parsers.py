import ast
import unittest
from pathlib import Path


class IndependentSearchTests(unittest.TestCase):
    def test_application_has_no_external_search_provider_registry(self):
        source = (Path(__file__).resolve().parents[1] / "app" / "main.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        names = {node.name for node in tree.body if isinstance(node, (ast.FunctionDef, ast.ClassDef))}
        self.assertNotIn("search_google", names)
        self.assertNotIn("search_bing", names)
        self.assertNotIn("search_brave", names)
        self.assertNotIn("search_duckduckgo", names)
        self.assertNotIn("SEARCHERS", names)
        self.assertIn("SearchWorker", names)

    def test_discovery_module_does_not_call_search_engine_hosts(self):
        source = (Path(__file__).resolve().parents[1] / "app" / "discovery.py").read_text(encoding="utf-8").lower()
        self.assertNotIn("bing.com", source)
        self.assertNotIn("google.com", source)
        self.assertNotIn("duckduckgo.com", source)

    def test_seed_catalog_exists_and_contains_http_urls(self):
        import json
        path = Path(__file__).resolve().parents[1] / "app" / "seed_catalog.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        self.assertGreaterEqual(len(data["seeds"]), 10)
        self.assertTrue(all(url.startswith("https://") for url in data["seeds"]))


if __name__ == "__main__":
    unittest.main()
