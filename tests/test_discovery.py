import tempfile
import unittest
from pathlib import Path

from app.discovery import SourceDB, expand_queries, normalize_url, domain_of, PoliteCrawler

class DiscoveryTests(unittest.TestCase):
    def test_query_expansion_keeps_original_first_and_unique(self):
        self.assertEqual(expand_queries("  pompa ciepła  ")[0], "pompa ciepła")
        self.assertEqual(len(expand_queries("pompa")), len(set(expand_queries("pompa"))))
    def test_private_hosts_are_rejected(self):
        from app.discovery import is_public_host
        self.assertFalse(is_public_host("127.0.0.1"))
        self.assertFalse(is_public_host("localhost"))
    def test_normalize_rejects_non_http_and_credentials(self):
        self.assertEqual(normalize_url("https://example.com/a#b"), "https://example.com/a")
        self.assertEqual(normalize_url("javascript:alert(1)"), "")
        self.assertEqual(normalize_url("https://user:pass@example.com"), "")
    def test_domain_normalization(self):
        self.assertEqual(domain_of("https://www.example.com/a"), "example.com")
    def test_database_persists_sources_pages_and_offers(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = SourceDB(Path(tmp) / "db.sqlite3")
            db.save_source("example.com")
            db.save_page("https://example.com/a","example.com","Pompa","Opis")
            db.save_offer("https://example.com/a","example.com","Pompa","123","PLN","Opis","pompa")
            self.assertEqual(db.known_sources()[0]["domain"],"example.com")
            self.assertEqual(db.offers_for_query("pompa")[0]["price"],"123")
    def test_price_expression_detects_polish_currency(self):
        from app.discovery import PRICE_RE
        match = PRICE_RE.search('Cena: 1 249,99 zł brutto')
        self.assertIsNotNone(match)
        self.assertEqual(match.group(1), '1 249,99')
    def test_extract_detects_jsonld_offer_and_internal_links(self):
        with tempfile.TemporaryDirectory() as tmp:
            crawler = PoliteCrawler(SourceDB(Path(tmp) / "db.sqlite3"))
            html = """<html><head><title>Pompa</title>
            <script type="application/ld+json">{"@context":"https://schema.org","@type":"Product","name":"Pompa","offers":{"@type":"Offer","price":"123.00","priceCurrency":"PLN"}}</script>
            </head><body><p>Produkt dostępny w sklepie, cena 123 zł</p>
            <a href="/oferta">Oferta</a><a href="https://other.example/x">Obcy</a></body></html>"""
            title, snippet, offer, links = crawler.extract("https://example.com/","".join(html),"pompa")
            self.assertEqual(title,"Pompa")
            self.assertTrue(offer[0])
            self.assertEqual(offer[1],"123.00")
            self.assertIn("https://example.com/oferta",links)
            self.assertTrue(any("other.example" in x for x in links))

if __name__ == "__main__":
    unittest.main()
