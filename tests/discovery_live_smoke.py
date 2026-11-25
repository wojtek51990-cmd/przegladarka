"""Small live discovery smoke test; crawler restrictions are not release failures."""
import tempfile
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import requests
import xml.etree.ElementTree as ET

from app.discovery import HEADERS, PoliteCrawler, SourceDB, normalize_url, domain_of

QUERY = "sprężarka Embraco EMT"

def main():
    response = requests.get("https://www.bing.com/search",
        params={"format":"rss","q":QUERY}, headers=HEADERS, timeout=14)
    response.raise_for_status()
    root = ET.fromstring(response.content)
    seeds = []
    for item in root.findall(".//item"):
        url = normalize_url(item.findtext("link",""))
        if url and domain_of(url) not in ("bing.com","microsoft.com"):
            seeds.append(url)
    seeds = list(dict.fromkeys(seeds))[:5]
    print(f"Search seeds: {len(seeds)}")
    if not seeds:
        print("No search-engine seed available; testing crawler with a direct public URL instead.")
        seeds = ["https://example.com/"]
    with tempfile.TemporaryDirectory() as tmp:
        crawler = PoliteCrawler(SourceDB(Path(tmp) / "smoke.sqlite3"), max_pages=4, pages_per_domain=1)
        offers = crawler.crawl(QUERY, seeds, deep=False)
        print(f"Pages checked: {crawler.pages_checked}")
        print(f"Query-matching product offers: {len(offers)}")
        for offer in offers[:10]:
            print(f"- {offer.get('title', '')} | {offer.get('price', '')} {offer.get('currency', '')} | {offer.get('url', '')}")
        print(f"Polite-crawl notes: {len(crawler.errors)}")
        if crawler.pages_checked == 0:
            raise SystemExit("Crawler could not fetch any allowed public HTML page from the supplied smoke-test seeds.")

if __name__ == "__main__":
    main()
