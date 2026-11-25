"""Probe public search endpoints from GitHub runner; print ASCII-safe diagnostics."""
import requests
from bs4 import BeautifulSoup
from urllib.parse import quote_plus

Q = "sprężarka Embraco EMT"
BASE = quote_plus(Q)
URLS = {
    "bing_rss": "https://www.bing.com/search?format=rss&q=" + BASE,
    "bing_html": "https://www.bing.com/search?q=" + BASE,
    "google": "https://www.google.com/search?gbv=1&q=" + BASE,
    "ecosia": "https://www.ecosia.org/search?q=" + BASE,
    "brave": "https://search.brave.com/search?q=" + BASE,
    "startpage": "https://www.startpage.com/sp/search?query=" + BASE,
    "searx_tiekoetter": "https://searx.tiekoetter.com/search?q=" + BASE + "&format=json",
    "searx_be": "https://searx.be/search?q=" + BASE + "&format=json",
    "searx_sapti": "https://search.sapti.me/search?q=" + BASE + "&format=json",
    "searx_ononoki": "https://search.ononoki.org/search?q=" + BASE + "&format=json",
    "ddg_lite": "https://lite.duckduckgo.com/lite/?q=" + BASE,
}
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126.0.0.0 Safari/537.36",
    "Accept": "application/json,application/rss+xml,application/xml,text/html,*/*",
}
for name, url in URLS.items():
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        ctype = r.headers.get("content-type", "")
        if "json" in ctype:
            try:
                data = r.json()
                items = data.get("results", []) if isinstance(data, dict) else []
                preview = [x.get("title", "") for x in items[:3]]
                print(f"{name}: HTTP {r.status_code}; type={ctype}; items={len(items)}; preview={ascii(preview)}")
                continue
            except Exception:
                pass
        soup = BeautifulSoup(r.text, "html.parser")
        title = soup.title.get_text(" ", strip=True) if soup.title else ""
        anchors = [a.get_text(" ", strip=True)[:80] for a in soup.select("a[href]") if a.get_text(" ", strip=True)]
        print(f"{name}: HTTP {r.status_code}; type={ctype}; title={ascii(title[:100])}; bytes={len(r.content)}; links={ascii(anchors[:6])}; body={ascii(soup.get_text(' ', strip=True)[:220])}")
    except Exception as exc:
        print(f"{name}: ERROR {ascii(str(exc))}")
