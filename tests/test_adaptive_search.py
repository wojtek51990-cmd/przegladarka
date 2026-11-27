import unittest
from bs4 import BeautifulSoup

from app.adaptive_search import (
    discover_search_requests, extract_adaptive_result_cards, inspect_search_page,
)


class AdaptiveSearchTests(unittest.TestCase):
    def test_discovers_unbranded_get_form_without_platform_fingerprint(self):
        html = """<html><body><form action="/find" method="get">
          <input name="phrase" placeholder="Szukaj w naszym sklepie">
          <input type="hidden" name="category" value="all">
        </form></body></html>"""
        requests = discover_search_requests(BeautifulSoup(html, "html.parser"),
                                            "https://independent.example/")
        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0].method, "GET")
        self.assertEqual(requests[0].field, "phrase")
        self.assertEqual(requests[0].payload("czujnik DS18B20"),
                         {"phrase": "czujnik DS18B20", "category": "all"})

    def test_discovers_only_explicit_same_origin_post_search_form(self):
        html = """<html><body>
          <form action="/catalog/query" method="post">
            <input type="search" name="term">
            <input type="hidden" name="csrf_token" value="secret">
          </form>
          <form action="https://other.example/search" method="post">
            <input type="search" name="q">
          </form>
          <form action="/account/login" method="post">
            <input name="login"><input type="password" name="password">
          </form>
        </body></html>"""
        requests = discover_search_requests(BeautifulSoup(html, "html.parser"),
                                            "https://independent.example/")
        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0].method, "POST")
        self.assertEqual(requests[0].field, "term")
        self.assertNotIn("csrf_token", dict(requests[0].data))

    def test_detects_custom_product_cards_without_known_classes_or_product_urls(self):
        html = """<html><body><main>
          <section><div class="tile-a"><a href="/x/100"><img src="/a.jpg">
            <h3>Termometr DS18B20</h3></a><span>12,50 zł</span></div>
          <div class="tile-a"><a href="/x/101"><img src="/b.jpg">
            <h3>Termometr DS18B20 wodoodporny</h3></a><span>18,90 zł</span></div>
          </section></main></body></html>"""
        rows = extract_adaptive_result_cards(BeautifulSoup(html, "html.parser"),
                                             "https://independent.example/search?term=DS18B20")
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["url"], "https://independent.example/x/100")
        self.assertEqual(rows[0]["price"], "12,50")
        self.assertEqual(rows[1]["price"], "18,90")

    def test_price_is_scoped_to_its_card_not_page_banner(self):
        html = """<html><body><div class="promo">99 zł tylko dziś</div>
          <main><section><div class="tile-a"><a href="/x/100"><h3>Produkt A</h3></a>
          <span class="amount">12,50 zł</span></div>
          <div class="tile-a"><a href="/x/101"><h3>Produkt B</h3></a>
          <span class="amount">18,90 zł</span></div></section></main></body></html>"""
        rows = extract_adaptive_result_cards(BeautifulSoup(html, "html.parser"),
                                             "https://independent.example/search?q=produkt")
        self.assertEqual([row["price"] for row in rows], ["12,50", "18,90"])

    def test_does_not_treat_navigation_or_article_links_as_product_cards(self):
        html = """<html><body><nav><a href="/category">DS18B20</a></nav>
          <article><a href="/news/1">Poradnik DS18B20</a><p>12,50 zł</p></article>
          <footer><a href="/contact">Kontakt</a></footer></body></html>"""
        rows = extract_adaptive_result_cards(BeautifulSoup(html, "html.parser"),
                                             "https://independent.example/search?q=DS18B20")
        self.assertEqual(rows, [])

    def test_custom_path_post_search_response_is_parsed_as_store_results(self):
        from app.site_architecture import extract_store_search_results
        html = """<html><body>
          <div class="tile-a"><a href="/sku/10"><h3>DS18B20 czujnik</h3></a><b>15,00 zł</b></div>
          <div class="tile-a"><a href="/sku/11"><h3>DS18B20 czujnik wodoodporny</h3></a><b>18,00 zł</b></div>
        </body></html>"""
        soup = BeautifulSoup(html, "html.parser")
        rows = extract_store_search_results(
            soup, "https://independent.example/catalog/query", "DS18B20", force_search=True
        )
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["store_returned_cards"], 2)

    def test_search_page_report_separates_store_count_from_cards_read(self):
        html = """<html><body><p class="results-count">37 produktów</p>
          <div class="item-x"><a href="/sku/1"><h3>Produkt pierwszy</h3></a><b>10,00 zł</b></div>
          <div class="item-x"><a href="/sku/2"><h3>Produkt drugi</h3></a><b>11,00 zł</b></div>
        </body></html>"""
        report = inspect_search_page(BeautifulSoup(html, "html.parser"),
                                     "https://independent.example/search?q=x")
        self.assertEqual(report["store_total_count"], 37)
        self.assertEqual(report["returned_cards"], 2)


if __name__ == "__main__":
    unittest.main()
