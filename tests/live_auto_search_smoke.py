"""Live regression for automatic first-party shop search discovery.

No per-shop search URLs are supplied: test targets come from app/seed_catalog.json.
The crawler must detect public GET search forms itself and follow the generated URL.
"""
import json
import tempfile
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.discovery import PoliteCrawler, SourceDB, load_seed_catalog


QUERY = "czujnik temperatury DS18B20"
# Only real retail/distributor domains already present in the application's
# seed catalog. Their search URLs are deliberately NOT hard-coded here.
RETAIL_HOST_HINTS = (
    "botland.com.pl", "kamami.pl", "elhurt.com.pl", "north.pl",
    "tim.pl", "conrad.pl", "rs-online.com", "farnell.com",
    "nettigo.pl", "sklep.avt.pl", "24volt.eu",
)


class AuditedCrawler(PoliteCrawler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.detected_searches = []

    def extract(self, url, html, query):
        result = super().extract(url, html, query)
        for search_url in getattr(self, "_last_search_form_links", set()):
            if search_url not in self.detected_searches:
                self.detected_searches.append(search_url)
        return result


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    seeds = [
        url for url in load_seed_catalog()
        if any(host in url.lower() for host in RETAIL_HOST_HINTS)
    ]
    if not seeds:
        raise SystemExit("FAIL: no retail seed domains found in seed_catalog.json")

    total_pages = 0
    total_searches = 0
    total_returned_cards = 0
    total_reported_products = 0
    total_search_pages = 0
    tested = 0
    reports = []

    with tempfile.TemporaryDirectory() as tmp:
        for index, seed in enumerate(seeds, 1):
            db = SourceDB(Path(tmp) / f"shop-{index}.sqlite3")
            crawler = AuditedCrawler(db, max_pages=6, pages_per_domain=6)
            try:
                offers = crawler.crawl(QUERY, [seed], deep=False)
                tested += 1
                total_pages += crawler.pages_checked
                total_searches += len(crawler.detected_searches)
                search_reports = crawler.store_search_reports
                total_search_pages += len(search_reports)
                total_returned_cards += sum(x["returned_cards"] for x in search_reports)
                total_reported_products += sum(
                    x["store_reported_total"] or 0 for x in search_reports
                )
                host = seed.split("//", 1)[-1].split("/", 1)[0]
                reports.append({
                    "host": host,
                    "pages": crawler.pages_checked,
                    "auto_search_urls": crawler.detected_searches,
                    "search_reports": search_reports,
                    "returned_cards": sum(x["returned_cards"] for x in search_reports),
                    "errors": crawler.errors[:2],
                })
            except Exception as exc:
                host = seed.split("//", 1)[-1].split("/", 1)[0]
                reports.append({
                    "host": host, "pages": 0, "auto_search_urls": [],
                    "search_reports": [], "returned_cards": 0,
                    "errors": [type(exc).__name__],
                })

    print(f"Query: {QUERY}")
    print("Manually supplied shop-search URLs: 0")
    print(f"Retail seed domains tested: {tested}")
    print(f"Pages checked: {total_pages}")
    print(f"Automatically detected first-party search forms: {total_searches}")
    print(f"Store search pages actually checked: {total_search_pages}")
    print(f"Product cards read from store results: {total_returned_cards}")
    print(f"Products reported by stores (sum; may include pagination): {total_reported_products}")
    for item in reports:
        print(
            f"- {item['host']}: pages={item['pages']}, "
            f"auto_search_forms={len(item['auto_search_urls'])}, "
            f"store_search_pages={len(item['search_reports'])}, "
            f"returned_cards={item['returned_cards']}, errors={item['errors']}"
        )
        for url in item["auto_search_urls"][:2]:
            print(f"  AUTO_SEARCH_URL: {url}")
        for report in item["search_reports"]:
            print(f"  STORE_SEARCH: status={report['status']}, "
                  f"store_total={report['store_reported_total']}, "
                  f"cards_read={report['returned_cards']}, "
                  f"product_exists={report['product_exists']}")

    # A live regression is meaningful only if the crawler reaches at least
    # one real shop's public GET search form without a manually supplied URL.
    if tested < 3:
        raise SystemExit("FAIL: fewer than three retail domains were tested")
    if total_searches == 0:
        raise SystemExit("FAIL: no first-party GET search form was auto-detected")


if __name__ == "__main__":
    main()
