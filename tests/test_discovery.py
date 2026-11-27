import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.discovery import (
    SourceDB, expand_queries, normalize_url, domain_of, PoliteCrawler,
    product_match_score, is_adult_content, site_search_urls,
)
from app.site_architecture import (
    detect_site_architecture, platform_search_urls, extract_product_metadata,
    extract_store_search_results,
)


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

    def test_product_match_requires_query_terms(self):
        self.assertEqual(product_match_score("krem nivea soft", "Krem Nivea Soft 200 ml"), 1.0)
        self.assertLess(product_match_score("krem nivea soft", "Opole atrakcje Wikipedia"), 0.34)
        self.assertGreaterEqual(product_match_score("sprężarka Embraco EMT", "Sprężarka Embraco EMT"), 1.0)

    def test_adult_sites_are_detected(self):
        self.assertTrue(is_adult_content("https://xxx.example/", "Portal", ""))
        self.assertTrue(is_adult_content("https://example.com/", "Portal", "treści pornograficzne"))
        self.assertFalse(is_adult_content("https://example.com/", "Sklep z kremami", "Koszyk cena"))

    def test_database_only_returns_verified_sources_and_matching_offers(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = SourceDB(Path(tmp) / "db.sqlite3")
            db.save_source("junk.example", "strona / inne")
            db.save_source("shop.example", "sklep")
            db.save_page("https://shop.example/nivea", "shop.example", "Krem Nivea Soft", "Krem 200 ml")
            db.save_offer("https://shop.example/nivea", "shop.example", "Krem Nivea Soft", "19,99", "PLN", "Krem 200 ml", "krem nivea soft")
            db.save_offer("https://junk.example/opole", "junk.example", "Opole atrakcje", "19,99", "PLN", "Wikipedia", "krem nivea soft")
            self.assertEqual([s["domain"] for s in db.known_sources()], ["shop.example"])
            self.assertEqual(len(db.offers_for_query("krem nivea soft")), 1)

    def test_extract_detects_matching_product_jsonld_and_price(self):
        with tempfile.TemporaryDirectory() as tmp:
            crawler = PoliteCrawler(SourceDB(Path(tmp) / "db.sqlite3"))
            html = """<html><head><title>Krem Nivea Soft 200 ml</title>
            <script type="application/ld+json">{"@context":"https://schema.org","@type":"Product","name":"Nivea Soft krem","offers":{"@type":"Offer","price":"19.99","priceCurrency":"PLN"}}</script>
            </head><body><p>Produkt dostępny w sklepie, cena 19,99 zł</p>
            <a href="/produkt/nivea-soft">Krem Nivea Soft</a>
            <a href="https://other.example/unrelated">Obcy portal</a></body></html>"""
            title, snippet, offer, links, source_type, commercial, relevance = crawler.extract(
                "https://example.com/", html, "krem nivea soft"
            )
            self.assertEqual(title, "Krem Nivea Soft 200 ml")
            self.assertTrue(offer[0])
            self.assertEqual(offer[1], "19.99")
            self.assertEqual(source_type, "sklep")
            self.assertTrue(commercial)
            self.assertGreaterEqual(relevance, 0.75)
            self.assertIn("https://example.com/produkt/nivea-soft", links)
            self.assertFalse(any("other.example" in link for link in links))

    def test_unrelated_forum_is_not_offer_or_commercial_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            crawler = PoliteCrawler(SourceDB(Path(tmp) / "db.sqlite3"))
            html = """<html><head><title>Opole - forum dyskusyjne</title></head>
            <body><p>Użytkownicy rozmawiają o kremach i cenach w Opolu.</p>
            <a href="/temat/123">Dyskusja</a></body></html>"""
            _, _, offer, _, _, commercial, relevance = crawler.extract(
                "https://forum.example/", html, "krem nivea soft"
            )
            self.assertFalse(offer[0])
            self.assertFalse(commercial)
            self.assertLess(relevance, 0.75)

    def test_crawler_never_follows_external_links(self):
        with tempfile.TemporaryDirectory() as tmp:
            crawler = PoliteCrawler(SourceDB(Path(tmp) / "db.sqlite3"), max_pages=5, pages_per_domain=5)
            html = """<html><head><title>Sklep z kremami</title></head><body>
            <p>Sklep, produkty, cena, koszyk</p><a href="https://external.example/item">zewnętrzna</a>
            </body></html>"""
            with patch.object(crawler, "fetch", return_value=("https://shop.example/", html, "text/html")) as fetch:
                with patch.object(crawler, "sitemap_urls", return_value=[]):
                    crawler.crawl("krem nivea soft", ["https://shop.example/"])
            self.assertEqual(fetch.call_count, 1)

    def test_crawler_does_not_invent_results_from_product_pages_outside_store_search(self):
        with tempfile.TemporaryDirectory() as tmp:
            crawler = PoliteCrawler(SourceDB(Path(tmp) / "db.sqlite3"), max_pages=5, pages_per_domain=5)
            home = """<html><head><title>Sklep</title></head><body>
              <a href="/produkt/czujnik-ds18b20">Czujnik DS18B20</a></body></html>"""
            product = """<html><head><title>Czujnik DS18B20</title>
              <meta property="og:type" content="product"></head>
              <body><p>DS18B20 cena 12,50 zł</p></body></html>"""
            def fake_fetch(url):
                if url.endswith("/"):
                    return url, home, "text/html"
                return url, product, "text/html"
            with patch.object(crawler, "fetch", side_effect=fake_fetch):
                with patch.object(crawler, "sitemap_urls", return_value=[]):
                    results = crawler.crawl("DS18B20", ["https://shop.example/"])
            self.assertEqual(results, [])

    def test_page_with_adult_content_is_not_saved_as_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = SourceDB(Path(tmp) / "db.sqlite3")
            crawler = PoliteCrawler(db)
            html = """<html><head><title>XXX adult portal</title></head>
            <body><p>sklep produkt cena koszyk</p></body></html>"""
            with patch.object(crawler, "fetch", return_value=("https://adult.example/", html, "text/html")):
                with patch.object(crawler, "sitemap_urls", return_value=[]):
                    crawler.crawl("produkt test", ["https://adult.example/"])
            self.assertEqual(db.known_sources(), [])
            self.assertEqual(crawler.pages_rejected, 1)

    def test_rossmann_direct_site_search_url_is_generated(self):
        urls = site_search_urls(["https://www.rossmann.pl/"], "krem nivea")
        self.assertEqual(urls, ["https://www.rossmann.pl/szukaj?Search=krem+nivea"])

    def test_generic_shop_get_search_form_is_detected_and_prioritized(self):
        with tempfile.TemporaryDirectory() as tmp:
            crawler = PoliteCrawler(SourceDB(Path(tmp) / "db.sqlite3"))
            html = """<html><head><title>Sklep</title></head><body>
            <form action="/szukaj" method="get">
              <input type="text" name="query">
              <input type="hidden" name="category" value="all">
            </form>
            <a href="/produkt/krem-nivea">Krem Nivea</a>
            </body></html>"""
            _, _, _, links, _, _, _ = crawler.extract(
                "https://shop.example/", html, "krem nivea"
            )
            self.assertEqual(
                links[0],
                "https://shop.example/szukaj?query=krem+nivea&category=all"
            )

    def test_generic_shop_post_search_form_is_not_submitted(self):
        with tempfile.TemporaryDirectory() as tmp:
            crawler = PoliteCrawler(SourceDB(Path(tmp) / "db.sqlite3"))
            html = """<html><body>
            <form action="/search" method="post">
              <input type="search" name="q">
            </form></body></html>"""
            _, _, _, links, _, _, _ = crawler.extract(
                "https://shop.example/", html, "krem nivea"
            )
            self.assertFalse(any("/search?" in link for link in links))

    def test_shop_homepage_is_not_confirmed_as_product_offer(self):
        with tempfile.TemporaryDirectory() as tmp:
            crawler = PoliteCrawler(SourceDB(Path(tmp) / "db.sqlite3"))
            html = """<html><head><title>Drogeria Rossmann - Strona główna</title></head>
            <body><p>Sklep, produkty, cena 7,29 zł, koszyk. Krem NIVEA.</p></body></html>"""
            _, _, offer, _, _, _, _ = crawler.extract(
                "https://www.rossmann.pl/", html, "krem nivea"
            )
            self.assertFalse(offer[0])

    def test_rossmann_search_product_links_are_prioritized(self):
        with tempfile.TemporaryDirectory() as tmp:
            crawler = PoliteCrawler(SourceDB(Path(tmp) / "db.sqlite3"), max_pages=5, pages_per_domain=5)
            search_url = "https://www.rossmann.pl/szukaj?Search=krem+nivea"
            product_url = "https://www.rossmann.pl/Produkt/Kremy/Nivea-Creme,9399"
            other_url = "https://other.example/"
            search_html = """<html><head><title>krem nivea - wyniki</title></head>
            <body><a href="/Produkt/Kremy/Nivea-Creme,9399">NIVEA Creme krem 50 ml</a>
            <a href="/kategorie/pielegnacja">Pielęgnacja</a></body></html>"""
            product_html = """<html><head><title>NIVEA Creme krem uniwersalny 50 ml</title>
            <meta property="og:type" content="product"></head>
            <body><p>Krem NIVEA 7,29 zł. Dodaj do koszyka.</p></body></html>"""
            other_html = "<html><head><title>Inna strona</title></head><body>tekst</body></html>"
            def fake_fetch(url):
                if url == search_url:
                    return search_url, search_html, "text/html"
                if url == product_url:
                    return product_url, product_html, "text/html"
                return other_url, other_html, "text/html"
            with patch.object(crawler, "fetch", side_effect=fake_fetch) as fetch:
                with patch.object(crawler, "sitemap_urls", return_value=[]):
                    crawler.crawl("krem nivea", [search_url, other_url], deep=True)
            fetched_urls = [call.args[0] for call in fetch.call_args_list]
            self.assertEqual(fetched_urls[:2], [search_url, product_url])

    def test_rossmann_product_detail_with_price_is_confirmed(self):
        with tempfile.TemporaryDirectory() as tmp:
            crawler = PoliteCrawler(SourceDB(Path(tmp) / "db.sqlite3"))
            html = """<html><head><title>NIVEA Creme krem uniwersalny 50 ml</title>
            <meta property="og:type" content="product"></head>
            <body><h1>NIVEA Creme</h1><p>Krem uniwersalny do twarzy, ciała i dłoni 50 ml</p>
            <p>7,29 zł</p><button>Dodaj do koszyka</button></body></html>"""
            _, _, offer, _, _, _, score = crawler.extract(
                "https://www.rossmann.pl/Produkt/Kremy/Nivea-Creme,9399", html, "krem nivea"
            )
            self.assertTrue(offer[0])
            self.assertEqual(offer[1], "7,29")
            self.assertEqual(score, 1.0)


    def test_platform_fingerprints_select_different_store_architectures(self):
        cases = [
            ("<script src='/wp-content/plugins/woocommerce/assets/app.js'></script>", "woocommerce"),
            ("<meta name='generator' content='PrestaShop'><script src='/modules/x/views/a.js'></script>", "prestashop"),
            ("<script>requirejs.config({}); var mage = true;</script><div class='catalogsearch'></div>", "magento"),
            ("<script src='https://cdn.shopify.com/shop.js'></script>", "shopify"),
            ("<a href='index.php?route=product/search'>Szukaj</a>", "opencart"),
        ]
        for html, expected in cases:
            with self.subTest(expected=expected):
                architecture = detect_site_architecture(html)
                self.assertEqual(architecture["platform"], expected)
                self.assertGreater(architecture["confidence"], 0)

    def test_platform_adapters_generate_first_party_search_urls(self):
        origin = "https://shop.example/"
        cases = {
            "woocommerce": "/?s=czujnik+DS18B20&post_type=product",
            "magento": "/catalogsearch/result/?q=czujnik+DS18B20",
            "shopify": "/search?q=czujnik+DS18B20",
            "opencart": "/index.php?route=product%2Fsearch&search=czujnik+DS18B20",
        }
        for platform, expected_path in cases.items():
            with self.subTest(platform=platform):
                urls = platform_search_urls(
                    origin, "czujnik DS18B20",
                    {"platform": platform, "confidence": 0.8},
                )
                self.assertEqual(len(urls), 1)
                self.assertEqual(urls[0], origin.rstrip("/") + expected_path)

    def test_unknown_architecture_does_not_guess_search_route(self):
        self.assertEqual(
            platform_search_urls("https://shop.example/", "produkt",
                                 {"platform": "custom_or_unknown"}),
            [],
        )

    def test_store_search_results_are_read_as_returned_without_stock_gate(self):
        from bs4 import BeautifulSoup
        html = """<html><body>
          <div class="product-card">
            <a class="product-title" href="/produkt/czujnik-ds18b20">Czujnik temperatury DS18B20</a>
            <span class="price">12,50 zł</span>
            <span class="availability">Na zamówienie</span>
          </div>
          <div class="product-card">
            <a href="/produkt/czujnik-wodoodporny">Czujnik DS18B20 wodoodporny</a>
            <span class="price">18,00 zł</span>
          </div>
        </body></html>"""
        rows = extract_store_search_results(BeautifulSoup(html, "html.parser"),
            "https://shop.example/search?q=DS18B20", "DS18B20")
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["price"], "12,50")
        self.assertEqual(rows[0]["availability"], "Na zamówienie")
        self.assertEqual(rows[1]["availability"], "")

    def test_store_results_are_not_extracted_from_regular_homepage(self):
        from bs4 import BeautifulSoup
        html = """<html><body><div class="product-card">
          <a href="/produkt/test">Test product</a></div></body></html>"""
        self.assertEqual(extract_store_search_results(
            BeautifulSoup(html, "html.parser"), "https://shop.example/", "test product"), [])

    def test_product_metadata_reads_opengraph_and_microdata(self):
        from bs4 import BeautifulSoup
        soup = BeautifulSoup("""<html><head>
          <meta property="og:type" content="product">
          <meta property="og:title" content="Czujnik DS18B20">
          <meta property="product:price:amount" content="12.50">
          <meta property="product:price:currency" content="PLN">
        </head><body></body></html>""", "html.parser")
        data = extract_product_metadata(soup)
        self.assertTrue(data["is_product"])
        self.assertEqual(data["name"], "Czujnik DS18B20")
        self.assertEqual(data["price"], "12.50")
        self.assertEqual(data["currency"], "PLN")


if __name__ == "__main__":
    unittest.main()
