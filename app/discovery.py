"""Independent public-web offer discovery engine.

The crawler stays polite: it checks robots.txt, rate-limits each host, never logs
in, never bypasses CAPTCHAs, and only follows ordinary public HTTP(S) pages.
"""
from __future__ import annotations

import json
from contextlib import contextmanager
import re
import sqlite3
import time
import urllib.robotparser
import xml.etree.ElementTree as ET
from collections import deque
from pathlib import Path
from urllib.parse import urljoin, urlparse, urldefrag, quote_plus
from urllib.robotparser import RobotFileParser

import requests
from bs4 import BeautifulSoup

USER_AGENT = "PrzegladarkaOfferDiscovery/0.3 (+public-web; respectful crawler)"
HEADERS = {"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8", "Accept-Language": "pl-PL,pl;q=0.9,en;q=0.7"}
TIMEOUT = 12
MAX_BYTES = 2_000_000
PRICE_RE = re.compile(r"(?<!\w)(\d{1,3}(?:[ .]\d{3})*(?:,\d{2})?|\d+(?:[,.]\d{2})?)\s?(zł|PLN|EUR|€|USD|\$)", re.I)
OFFER_WORDS = ("cena", "zł", "pln", "eur", "produkt", "oferta", "sklep", "hurtownia", "producent", "dostępny", "dostepny", "koszyk", "brutto")

def domain_of(url: str) -> str:
    host = urlparse(url).hostname or ""
    return host.lower().removeprefix("www.")

def normalize_url(url: str) -> str:
    if not url:
        return ""
    url, _ = urldefrag(url.strip())
    p = urlparse(url)
    if p.scheme not in ("http", "https") or not p.hostname:
        return ""
    if p.username or p.password:
        return ""
    return url

def expand_queries(query: str) -> list[str]:
    q = " ".join(query.split())
    if not q:
        return []
    variants = [q, f'"{q}"', f"{q} cena", f"{q} sklep OR hurtownia", f"{q} producent"]
    # Keep the original query first; remove duplicates without changing order.
    return list(dict.fromkeys(variants))

class SourceDB:
    def __init__(self, path=None):
        if path is None:
            root = Path.home() / "AppData" / "Local" / "Przegladarka"
            root.mkdir(parents=True, exist_ok=True)
            path = root / "discovery.sqlite3"
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS sources(
              domain TEXT PRIMARY KEY, first_seen TEXT DEFAULT CURRENT_TIMESTAMP,
              last_seen TEXT DEFAULT CURRENT_TIMESTAMP, pages_checked INTEGER DEFAULT 0,
              source_type TEXT DEFAULT 'strona', notes TEXT DEFAULT '');
            CREATE TABLE IF NOT EXISTS pages(
              url TEXT PRIMARY KEY, domain TEXT NOT NULL, title TEXT, snippet TEXT,
              last_checked TEXT DEFAULT CURRENT_TIMESTAMP, status TEXT);
            CREATE TABLE IF NOT EXISTS offers(
              url TEXT PRIMARY KEY, domain TEXT NOT NULL, title TEXT, price TEXT,
              currency TEXT, snippet TEXT, query TEXT, found_at TEXT DEFAULT CURRENT_TIMESTAMP);
            CREATE INDEX IF NOT EXISTS idx_offers_domain ON offers(domain);
            """)
    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=20)
        db.row_factory = sqlite3.Row
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()
    def save_source(self, domain, kind="strona"):
        with self.connect() as db:
            db.execute("INSERT INTO sources(domain,source_type) VALUES(?,?) ON CONFLICT(domain) DO UPDATE SET last_seen=CURRENT_TIMESTAMP", (domain,kind))
    def save_page(self, url, domain, title, snippet, status="ok"):
        with self.connect() as db:
            db.execute("INSERT INTO pages(url,domain,title,snippet,status) VALUES(?,?,?,?,?) ON CONFLICT(url) DO UPDATE SET title=excluded.title,snippet=excluded.snippet,last_checked=CURRENT_TIMESTAMP,status=excluded.status", (url,domain,title,snippet,status))
            db.execute("UPDATE sources SET pages_checked=pages_checked+1,last_seen=CURRENT_TIMESTAMP WHERE domain=?", (domain,))
    def save_offer(self, url, domain, title, price, currency, snippet, query):
        with self.connect() as db:
            db.execute("INSERT INTO offers(url,domain,title,price,currency,snippet,query) VALUES(?,?,?,?,?,?,?) ON CONFLICT(url) DO UPDATE SET title=excluded.title,price=excluded.price,currency=excluded.currency,snippet=excluded.snippet,query=excluded.query", (url,domain,title,price,currency,snippet,query))
    def known_sources(self, limit=100):
        with self.connect() as db:
            return [dict(r) for r in db.execute("SELECT * FROM sources ORDER BY last_seen DESC LIMIT ?", (limit,))]
    def offers_for_query(self, query, limit=100):
        tokens = [t.lower() for t in re.findall(r"[\w-]{3,}", query)]
        with self.connect() as db:
            rows = [dict(r) for r in db.execute("SELECT * FROM offers ORDER BY found_at DESC LIMIT 500")]
        if not tokens:
            return rows[:limit]
        ranked = []
        for row in rows:
            hay = (row.get("title") or "") + " " + (row.get("snippet") or "")
            score = sum(1 for t in tokens if t in hay.lower())
            if score:
                ranked.append((score,row))
        ranked.sort(key=lambda x:x[0], reverse=True)
        return [r for _,r in ranked[:limit]]

class PoliteCrawler:
    def __init__(self, db: SourceDB, max_pages=24, pages_per_domain=5):
        self.db = db
        self.max_pages = max_pages
        self.pages_per_domain = pages_per_domain
        self.session = requests.Session()
        self.session.headers.update(HEADERS)
        self.robots = {}
        self.last_request = {}
        self.errors = []
        self.pages_checked = 0
    def allowed(self, url):
        p = urlparse(url)
        origin = f"{p.scheme}://{p.netloc}"
        if origin not in self.robots:
            rp = RobotFileParser()
            rp.set_url(urljoin(origin, "/robots.txt"))
            try:
                response = self.session.get(rp.url, timeout=5)
                if response.status_code == 200:
                    rp.parse(response.text.splitlines())
                elif response.status_code in (401,403):
                    self.robots[origin] = None
                    return False
                else:
                    rp.parse(["User-agent: *", "Allow: /"])
            except requests.RequestException:
                # On robots fetch failure, do not crawl this host.
                self.robots[origin] = None
                return False
            self.robots[origin] = rp
        rp = self.robots[origin]
        return bool(rp and rp.can_fetch(USER_AGENT, url))
    def fetch(self, url):
        host = domain_of(url)
        wait = 1.0 - (time.monotonic() - self.last_request.get(host, 0))
        if wait > 0:
            time.sleep(wait)
        self.last_request[host] = time.monotonic()
        if not self.allowed(url):
            return None
        try:
            response = self.session.get(url, timeout=TIMEOUT, stream=True, allow_redirects=True)
            if response.status_code in (401,403,429):
                self.errors.append(f"{host}: HTTP {response.status_code}")
                return None
            response.raise_for_status()
            ctype = response.headers.get("Content-Type","").lower()
            if "text/html" not in ctype and "application/xhtml" not in ctype and "xml" not in ctype:
                return None
            chunks, size = [], 0
            for chunk in response.iter_content(65536):
                size += len(chunk)
                if size > MAX_BYTES:
                    break
                chunks.append(chunk)
            body = b"".join(chunks).decode(response.encoding or "utf-8", "replace")
            return response.url, body, ctype
        except requests.RequestException as exc:
            self.errors.append(f"{host}: {exc.__class__.__name__}")
            return None
    def sitemap_urls(self, base_url, cap=20):
        p = urlparse(base_url)
        origin = f"{p.scheme}://{p.netloc}"
        robots_url = urljoin(origin, "/robots.txt")
        found = []
        try:
            response = self.session.get(robots_url, timeout=5)
            for line in response.text.splitlines():
                if line.lower().startswith("sitemap:"):
                    sm = line.split(":",1)[1].strip()
                    if domain_of(sm) == domain_of(base_url):
                        found.append(sm)
        except requests.RequestException:
            pass
        for sm in found[:3]:
            try:
                response = self.session.get(sm, timeout=8)
                if response.status_code != 200:
                    continue
                root = ET.fromstring(response.content)
                for loc in root.findall(".//{*}loc"):
                    u = normalize_url(loc.text or "")
                    if u and domain_of(u) == domain_of(base_url):
                        found.append(u)
                        if len(found) >= cap:
                            return found[:cap]
            except (requests.RequestException, ET.ParseError):
                continue
        return [u for u in found if u.startswith("http")][:cap]
    def extract(self, url, html, query):
        soup = BeautifulSoup(html, "html.parser")
        for tag in soup(["script","style","noscript","svg"]):
            tag.decompose()
        title = (soup.title.get_text(" ",strip=True) if soup.title else "")[:300]
        text = re.sub(r"\s+", " ", soup.get_text(" ",strip=True))
        snippet = text[:700]
        price_match = PRICE_RE.search(text)
        currency = price_match.group(2).upper() if price_match else ""
        price = price_match.group(1) if price_match else ""
        product_json = False
        for node in soup.select('script[type="application/ld+json"]'):
            try:
                data = json.loads(node.string or node.get_text())
                stack = data if isinstance(data,list) else [data]
                while stack:
                    obj = stack.pop()
                    if isinstance(obj,dict):
                        if obj.get("@type") in ("Product","Offer","AggregateOffer") or (isinstance(obj.get("@type"),list) and any(t in ("Product","Offer") for t in obj["@type"])):
                            product_json = True
                            offers = obj.get("offers",{})
                            if isinstance(offers,list): offers = offers[0] if offers else {}
                            if isinstance(offers,dict):
                                price = str(offers.get("price",price))
                                currency = str(offers.get("priceCurrency",currency))
                        stack.extend(v for v in obj.values() if isinstance(v,(dict,list)))
                    elif isinstance(obj,list):
                        stack.extend(obj)
            except (ValueError, TypeError):
                continue
        score = sum(1 for word in OFFER_WORDS if word in text.lower())
        offer = bool(price_match or product_json or score >= 3)
        links = []
        for a in soup.select("a[href]"):
            target = normalize_url(urljoin(url, a.get("href","")))
            if target and domain_of(target) == domain_of(url):
                if not any(x in urlparse(target).path.lower() for x in ("/login","/konto","/cart","/koszyk","/checkout","/polityka","/privacy")):
                    links.append(target)
        return title, snippet, (offer, price, currency), list(dict.fromkeys(links))[:100]
    def crawl(self, query, seed_urls, deep=False):
        max_pages = self.max_pages * (2 if deep else 1)
        per_domain = self.pages_per_domain * (2 if deep else 1)
        queue = deque()
        seen = set()
        for u in seed_urls:
            u = normalize_url(u)
            if u:
                queue.append(u)
        domain_counts = {}
        results = []
        while queue and len(seen) < max_pages:
            url = queue.popleft()
            if url in seen:
                continue
            seen.add(url)
            domain = domain_of(url)
            if not domain or domain_counts.get(domain,0) >= per_domain:
                continue
            self.db.save_source(domain)
            fetched = self.fetch(url)
            if not fetched:
                continue
            final_url, html, ctype = fetched
            if "xml" in ctype:
                try:
                    root = ET.fromstring(html)
                    for loc in root.findall(".//{*}loc"):
                        target = normalize_url(loc.text or "")
                        if target and domain_of(target)==domain and target not in seen:
                            queue.append(target)
                except ET.ParseError:
                    pass
                continue
            domain_counts[domain] = domain_counts.get(domain,0)+1
            title, snippet, offer_data, links = self.extract(final_url, html, query)
            self.pages_checked += 1
            self.db.save_page(final_url, domain, title, snippet)
            is_offer, price, currency = offer_data
            if is_offer:
                self.db.save_offer(final_url, domain, title, price, currency, snippet, query)
                results.append({"title":title or domain,"url":final_url,"domain":domain,"price":price,"currency":currency,"snippet":snippet[:350],"source":"Crawler"})
            if domain_counts[domain] == 1:
                for sm_url in self.sitemap_urls(final_url, cap=12 if deep else 6):
                    if sm_url not in seen:
                        queue.append(sm_url)
            for link in links:
                if link not in seen and len(queue) < max_pages*8:
                    queue.append(link)
        return results

def discover(query: str, deep=False, db_path=None):
    db = SourceDB(db_path)
    seed_urls, errors = [], []
    crawler = PoliteCrawler(db)
    session = requests.Session()
    session.headers.update(HEADERS)
    # Search engines are only seed-discovery channels, not the product's core index.
    for q in expand_queries(query):
        try:
            response = session.get("https://www.bing.com/search", params={"format":"rss","q":q}, timeout=TIMEOUT)
            response.raise_for_status()
            root = ET.fromstring(response.content)
            for item in root.findall(".//item"):
                link = item.findtext("link","")
                u = normalize_url(link)
                if u and domain_of(u) not in ("bing.com","microsoft.com"):
                    seed_urls.append(u)
        except (requests.RequestException, ET.ParseError) as exc:
            errors.append(f"Bing: {exc.__class__.__name__}")
            break
    # Add cached source domains as secondary candidates; don't blindly crawl every old URL.
    for source in db.known_sources(30):
        if source["domain"]:
            seed_urls.append("https://" + source["domain"] + "/")
    seed_urls = list(dict.fromkeys(seed_urls))
    fresh = crawler.crawl(query, seed_urls, deep=deep) if seed_urls else []
    cached = db.offers_for_query(query, 100)
    merged = {r["url"]:r for r in cached}
    for row in fresh:
        merged[row["url"]] = row
    return {"results":list(merged.values())[:150],"sources":db.known_sources(200),"pages_checked":crawler.pages_checked, "errors":errors + crawler.errors[:20], "seed_count":len(seed_urls)}
