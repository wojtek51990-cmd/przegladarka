"""Live regression: Rossmann first-party search must lead to real NIVEA product pages."""
import tempfile
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.discovery import discover

QUERY = "krem nivea"
ROSSMANN_SEARCH = "https://www.rossmann.pl/szukaj?Search=krem+nivea"

def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    with tempfile.TemporaryDirectory() as tmp:
        data = discover(QUERY, deep=True, db_path=Path(tmp) / "rossmann.sqlite3",
                        extra_seeds=[ROSSMANN_SEARCH])
    results = [r for r in data.get("results", [])
               if "rossmann.pl" in r.get("url", "").lower()
               and "nivea" in (r.get("title", "") + " " + r.get("snippet", "")).lower()]
    reports = [r for r in data.get("store_search_reports", [])
               if "rossmann.pl" in r.get("domain", "")]
    print("External search engines used: no")
    print(f"Pages checked: {data.get('pages_checked', 0)}")
    print(f"Direct site searches: {data.get('direct_site_searches', [])}")
    print(f"Rossmann search reports: {reports}")
    print(f"Rossmann NIVEA cards read from store search: {len(results)}")
    prices = [item.get("price", "") for item in results if item.get("price")]
    print(f"Distinct card-scoped prices: {len(set(prices))}")
    for item in results[:10]:
        print(f"- {item.get('title')} | {item.get('price','')} {item.get('currency','')} | {item.get('url')}")
    for error in data.get("errors", [])[:15]:
        print(f"NOTE: {error}")
    if not reports or not results:
        raise SystemExit("FAIL: Rossmann search result cards were not read from the store search page.")
    if not prices:
        raise SystemExit("FAIL: no price was read from inside a Rossmann product card.")
    if len(results) >= 5 and len(set(prices)) < 2:
        raise SystemExit("FAIL: product cards appear to share one page-level price instead of individual prices.")

if __name__ == "__main__":
    main()
