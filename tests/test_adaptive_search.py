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

    def test_rejects_embedded_google_custom_search_form(self):
        html = """<html><body><form action="/links2.php" method="get">
          <input name="q"><input type="hidden" name="sitesearch" value="shop.example">
          <input type="hidden" name="client" value="pub-example">
          <input type="hidden" name="forid" value="1">
        </form></body></html>"""
        requests = discover_search_requests(
            BeautifulSoup(html, "html.parser"), "https://shop.example/"
        )
        self.assertEqual(requests, [])

    def test_search_page_discards_unrelated_catalogue_cards(self):
        html = """<html><body>
          <div class="tile-a"><a href="/sku/1"><h3>Czujnik DS18B20 wodoodporny</h3></a><b>15,00 zł</b></div>
          <div class="tile-a"><a href="/sku/2"><h3>Zasilacz uniwersalny 12V</h3></a><b>18,00 zł</b></div>
          <div class="tile-a"><a href="/sku/3"><h3>Termometr DS18B20</h3></a><b>22,00 zł</b></div>
        </body></html>"""
        report = inspect_search_page(
            BeautifulSoup(html, "html.parser"),
            "https://independent.example/search?text=DS18B20",
            "czujnik temperatury DS18B20",
        )
        self.assertEqual(report["candidate_cards"], 3)
        self.assertEqual(report["returned_cards"], 2)
        self.assertEqual(report["rejected_nonmatching_cards"], 1)

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

    def test_reads_raw_first_party_json_product_response(self):
        payload = """{
          "total": 29,
          "products": [
            {"product_id": 101, "product_name": "Czujnik temperatury DS18B20",
             "product_url": "/czujnik-ds18b20", "final_price": "12.50", "currency": "PLN"},
            {"product_id": 102, "product_name": "Moduł przekaźnika 12V",
             "product_url": "/modul-przekaznika", "final_price": "8.90", "currency": "PLN"}
          ]
        }"""
        report = inspect_search_page(
            BeautifulSoup(payload, "html.parser"),
            "https://shop.example/module/search?q=DS18B20", "DS18B20",
        )
        self.assertEqual(report["candidate_cards"], 2)
        self.assertEqual(report["returned_cards"], 1)
        self.assertEqual(report["cards"][0]["title"], "Czujnik temperatury DS18B20")
        self.assertEqual(report["cards"][0]["price"], "12.50")
        self.assertEqual(report["cards"][0]["url"], "https://shop.example/czujnik-ds18b20")

    def test_reads_html_result_fragment_wrapped_in_json(self):
        import json
        payload = {"html": '<div class="tile-a"><a href="/item/1"><h3>DS18B20 czujnik</h3></a>'
                            '<b>15,00 zł</b></div>'
                            '<div class="tile-a"><a href="/item/2"><h3>DS18B20 sonda</h3></a>'
                            '<b>18,00 zł</b></div>'}
        report = inspect_search_page(
            BeautifulSoup(json.dumps(payload), "html.parser"),
            "https://shop.example/ajax/search", "DS18B20",
        )
        self.assertEqual(report["returned_cards"], 2)
        self.assertEqual([row["price"] for row in report["cards"]], ["15,00", "18,00"])

    def test_raw_json_rejects_external_product_links(self):
        payload = """{"products":[
          {"product_id":1,"product_name":"DS18B20 czujnik",
           "product_url":"https://other.example/item/1","price":"10.00"}
        ]}"""
        report = inspect_search_page(
            BeautifulSoup(payload, "html.parser"),
            "https://shop.example/search?q=DS18B20", "DS18B20",
        )
        self.assertEqual(report["returned_cards"], 0)

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
