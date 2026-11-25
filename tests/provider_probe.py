"""Probe public search result endpoints from the GitHub Windows runner."""
import requests
from bs4 import BeautifulSoup
from urllib.parse import quote_plus

Q = "sprężarka Embraco EMT"
URLS = {
    "bing_rss": "https://www.bing.com/search?format=rss&q=" + quote_plus(Q),
    "bing_html": "https://www.bing.com/search?q=" + quote_plus(Q),
    "yahoo": "https://search.yahoo.com/search?p=" + quote_plus(Q),
    "google": "https://www.google.com/search?gbv=1&q=" + quote_plus(Q),
    "mojeek": "https://www.mojeek.com/search?q=" + quote_plus(Q),
    "ddg_lite": "https://lite.duckduckgo.com/lite/?q=" + quote_plus(Q),
    "ddg_html": "https://html.duckduckgo.com/html/?q=" + quote_plus(Q),
}

for name, url in URLS.items():
    try:
        r = requests.get(url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        }, timeout=20)
        soup = BeautifulSoup(r.text, "html.parser")
        title = soup.title.get_text(" ", strip=True) if soup.title else ""
        links = [a.get_text(" ", strip=True)[:80] for a in soup.select("a[href]") if a.get_text(" ", strip=True)]
        print(f"{name}: HTTP {r.status_code}; final={r.url[:120]}; title={title[:120]!r}; bytes={len(r.content)}; links={links[:8]!r}; body={soup.get_text(' ', strip=True)[:260]!r}")
    except Exception as exc:
        print(f"{name}: ERROR {ascii(str(exc))}")
