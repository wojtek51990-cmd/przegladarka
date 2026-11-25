import unittest
from unittest.mock import patch

from app.main import clean_url, parse_bing_rss, search_bing, search_duckduckgo


class FakeResponse:
    def __init__(self, html, status_code=200):
        self.text = html
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

    @patch("app.main.requests.get")
    def test_duckduckgo_lite_results_are_parsed(self, mocked_get):
        mocked_get.return_value = FakeResponse("""
            <html><body><table><tr>
              <td><a class="result-link" href="https://shop.example/oferta">
                Sprężarka Embraco EMT
              </a></td>
              <td>Opis produktu</td>
            </tr></table></body></html>
        """)

        results = search_duckduckgo("sprężarka Embraco EMT")

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["title"], "Sprężarka Embraco EMT")
        self.assertEqual(results[0]["url"], "https://shop.example/oferta")
        self.assertEqual(results[0]["engine"], "DuckDuckGo")

    @patch("app.main.requests.get")
    def test_duckduckgo_html_results_are_parsed(self, mocked_get):
        mocked_get.side_effect = [
            FakeResponse("<html><body>brak wyników w układzie lite</body></html>"),
            FakeResponse("""
                <html><body><div class="result">
                  <a class="result__a" href="https://hurtownia.example/czesci">
                    Hurtownia części
                  </a>
                  <a class="result__snippet">Części i podzespoły</a>
                </div></body></html>
            """),
        ]

        results = search_duckduckgo("części chłodnicze")

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["title"], "Hurtownia części")
        self.assertEqual(results[0]["url"], "https://hurtownia.example/czesci")
        self.assertEqual(mocked_get.call_count, 2)


    def test_bing_rss_feed_is_parsed(self):
        feed = b"""<?xml version="1.0" encoding="UTF-8"?>
        <rss version="2.0"><channel><item>
          <title>Sprężarka Embraco EMT</title>
          <link>https://sklep.example/embraco-emt</link>
          <description>Sprężarka do chłodnictwa</description>
        </item></channel></rss>"""
        results = parse_bing_rss(feed)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["title"], "Sprężarka Embraco EMT")
        self.assertEqual(results[0]["url"], "https://sklep.example/embraco-emt")
        self.assertEqual(results[0]["engine"], "Bing")

    def test_bing_tracking_link_is_decoded(self):
        import base64
        from urllib.parse import quote
        target = "https://sklep.example/czesci"
        encoded = "a1" + base64.urlsafe_b64encode(target.encode()).decode().rstrip("=")
        self.assertEqual(
            clean_url("https://www.bing.com/ck/a?u=" + quote(encoded)),
            target
        )

    @patch("app.main.requests.get")
    def test_bing_results_are_parsed(self, mocked_get):
        mocked_get.return_value = FakeResponse("""
            <html><body><ol>
              <li class="b_algo"><h2>
                <a href="https://sklep.example/silnik">Silnik elektryczny</a>
              </h2><p>Opis wyniku</p></li>
            </ol></body></html>
        """)

        results = search_bing("silnik elektryczny")

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["title"], "Silnik elektryczny")
        self.assertEqual(results[0]["url"], "https://sklep.example/silnik")
        self.assertEqual(results[0]["engine"], "Bing")

    @patch("app.main.requests.get")
    def test_bing_reports_unrecognized_page_instead_of_silent_empty_list(self, mocked_get):
        mocked_get.return_value = FakeResponse(
            "<html><body>Some changed search page</body></html>"
        )

        with self.assertRaisesRegex(RuntimeError, "nie udało się odczytać wyników"):
            search_bing("dowolne zapytanie")


if __name__ == "__main__":
    unittest.main()
