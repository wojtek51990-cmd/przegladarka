import unittest
from unittest.mock import patch

from app.main import (
    clean_url, parse_bing_rss, parse_search_html, search_bing,
    search_duckduckgo, merge_results,
)


class FakeResponse:
    def __init__(self, body, status_code=200):
        self.text = body if isinstance(body, str) else body.decode("utf-8", "replace")
        self.content = body if isinstance(body, bytes) else body.encode("utf-8")
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class SearchParserTests(unittest.TestCase):
    def test_clean_url_decodes_duckduckgo_redirect(self):
        url = (
            "https://duckduckgo.com/l/?uddg="
            "https%3A%2F%2Fexample.com%2Fprodukt%3Fid%3D7&rut=abc"
        )
        self.assertEqual(clean_url(url), "https://example.com/produkt?id=7")

    def test_clean_url_decodes_bing_tracking_link(self):
        import base64
        from urllib.parse import quote
        target = "https://sklep.example/czesci"
        encoded = "a1" + base64.urlsafe_b64encode(target.encode()).decode().rstrip("=")
        self.assertEqual(clean_url("https://www.bing.com/ck/a?u=" + quote(encoded)), target)

    def test_bing_rss_feed_is_parsed(self):
        feed = """<?xml version="1.0" encoding="UTF-8"?>
        <rss version="2.0"><channel><item>
          <title>Sprężarka Embraco EMT</title>
          <link>https://sklep.example/embraco-emt</link>
          <description>Sprężarka do chłodnictwa</description>
        </item></channel></rss>""".encode("utf-8")
        results = parse_bing_rss(feed)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["title"], "Sprężarka Embraco EMT")
        self.assertEqual(results[0]["engine"], "Bing")

    def test_google_result_layout_is_parsed(self):
        html = """
        <html><body><div class="MjjYud"><a href="https://sklep.example/produkt">
          <h3>Produkt w sklepie</h3></a><div class="VwiC3b">Opis produktu</div>
        </div></body></html>
        """
        rows = parse_search_html(html, "Google")
        self.assertEqual(rows[0]["url"], "https://sklep.example/produkt")
        self.assertEqual(rows[0]["engine"], "Google")

    def test_brave_result_layout_is_parsed(self):
        html = """
        <html><body><main><div class="snippet">
          <a data-testid="result-title" href="https://hurtownia.example/oferta">
          Hurtownia techniczna</a><p>Opis</p>
        </div></main></body></html>
        """
        rows = parse_search_html(html, "Brave Search")
        self.assertEqual(rows[0]["url"], "https://hurtownia.example/oferta")

    def test_mojeek_result_layout_is_parsed(self):
        html = '<html><body><a class="ob" href="https://parts.example/item">Część zamienna</a></body></html>'
        rows = parse_search_html(html, "Mojeek")
        self.assertEqual(rows[0]["engine"], "Mojeek")

    def test_generic_heading_layout_is_parsed_for_other_engines(self):
        for engine in ("Qwant", "Ecosia", "Startpage", "Yahoo"):
            with self.subTest(engine=engine):
                html = '<html><body><main><h2><a href="https://example.org/item">Wynik testowy</a></h2></main></body></html>'
                rows = parse_search_html(html, engine)
                self.assertEqual(rows[0]["url"], "https://example.org/item")

    def test_provider_self_links_are_excluded(self):
        html = '<html><body><h3><a href="https://www.google.com/preferences">Google preferences</a></h3></body></html>'
        with self.assertRaisesRegex(RuntimeError, "nie rozpoznano wyników"):
            parse_search_html(html, "Google")

    def test_merge_results_retains_all_engines(self):
        items = [
            {"title": "A", "url": "https://shop.example/item", "snippet": "", "engine": "Bing"},
            {"title": "A2", "url": "https://shop.example/item/", "snippet": "Opis", "engine": "Google"},
            {"title": "B", "url": "https://other.example/item", "snippet": "", "engine": "Brave Search"},
        ]
        merged = merge_results(items)
        self.assertEqual(len(merged), 2)
        self.assertEqual(merged[0]["engine"], "Bing, Google")
        self.assertEqual(merged[0]["snippet"], "Opis")

    @patch("app.main.requests.get")
    def test_duckduckgo_lite_results_are_parsed(self, mocked_get):
        mocked_get.return_value = FakeResponse("""
            <html><body><table><tr>
              <td><a class="result-link" href="https://shop.example/oferta">
                Sprężarka Embraco EMT
              </a></td><td>Opis produktu</td>
            </tr></table></body></html>
        """)
        results = search_duckduckgo("sprężarka Embraco EMT")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["engine"], "DuckDuckGo")

    @patch("app.main.requests.get")
    def test_duckduckgo_falls_back_to_second_endpoint(self, mocked_get):
        mocked_get.side_effect = [
            FakeResponse("<html><body>brak wyników w układzie lite</body></html>"),
            FakeResponse("""
                <html><body><div class="result">
                  <a class="result__a" href="https://hurtownia.example/czesci">
                    Hurtownia części
                  </a><a class="result__snippet">Części i podzespoły</a>
                </div></body></html>
            """),
        ]
        results = search_duckduckgo("części chłodnicze")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["url"], "https://hurtownia.example/czesci")
        self.assertEqual(mocked_get.call_count, 2)

    @patch("app.main.requests.get")
    def test_bing_prefers_rss(self, mocked_get):
        mocked_get.return_value = FakeResponse(b"""<rss><channel><item>
          <title>Silnik elektryczny</title><link>https://sklep.example/silnik</link>
          <description>Opis</description></item></channel></rss>""")
        results = search_bing("silnik elektryczny")
        self.assertEqual(results[0]["engine"], "Bing")
        self.assertEqual(mocked_get.call_count, 1)

    @patch("app.main.requests.get")
    def test_bing_falls_back_to_html(self, mocked_get):
        mocked_get.side_effect = [
            FakeResponse("<html><body>no rss items</body></html>"),
            FakeResponse("""
                <html><body><ol><li class="b_algo"><h2>
                  <a href="https://sklep.example/silnik">Silnik elektryczny</a>
                </h2><p>Opis wyniku</p></li></ol></body></html>
            """),
        ]
        results = search_bing("silnik elektryczny")
        self.assertEqual(results[0]["url"], "https://sklep.example/silnik")
        self.assertEqual(mocked_get.call_count, 2)

    @patch("app.main.requests.get")
    def test_bing_reports_unrecognized_page(self, mocked_get):
        mocked_get.side_effect = [
            FakeResponse("<html><body>no rss items</body></html>"),
            FakeResponse("<html><body>Some changed search page</body></html>"),
        ]
        with self.assertRaisesRegex(RuntimeError, "nie rozpoznano wyników"):
            search_bing("dowolne zapytanie")


if __name__ == "__main__":
    unittest.main()
