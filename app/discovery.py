"""Polite product-focused public-web discovery for Przegladarka 0.5.0.

Only verified commercial sources and query-matching product pages are persisted.
The crawler stays within each seed domain, obeys robots.txt and never bypasses
logins, CAPTCHAs, paywalls or anti-bot controls.
"""
from __future__ import annotations

import ipaddress
import json
import re
import socket
import sqlite3
import time
import urllib.robotparser
import xml.etree.ElementTree as ET
from collections import deque
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urljoin, urlparse, urldefrag, urlencode
from urllib.robotparser import RobotFileParser

import requests
from bs4 import BeautifulSoup

from app.site_architecture import (
    detect_site_architecture, platform_search_urls, extract_product_metadata,
    extract_store_search_results,
)

USER_AGENT = "PrzegladarkaIndependentCrawler/0.5 (+public-web; respectful crawler)"
HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "pl-PL,pl;q=0.9,en;q=0.7",
}
TIMEOUT = 12
MAX_BYTES = 2_000_000
PRICE_RE = re.compile(
    r"(?<!\w)(\d{1,3}(?:[ .]\d{3})*(?:,\d{2})?|\d+(?:[,.]\d{2})?)\s?(zł|PLN|EUR|€|USD|\$)",
    re.I,
)
VALID_SOURCE_TYPES = {"hurtownia / dystrybutor", "producent", "ogłoszenia / marketplace", "katalog", "sklep"}
STOP_WORDS = {
    "i", "oraz", "lub", "do", "na", "w", "z", "za", "the", "and", "for",
    "with", "of", "a", "an", "ml", "g", "kg", "szt", "sztuk",
}
ADULT_TERMS = (
    "porn", "porno", "pornografia", "xxx", "adult content", "sex shop",
    "sexshop", "hentai", "erotyczne", "erotyka", "webcam sex",
)
COMMERCIAL_TERMS = (
    "sklep", "koszyk", "dodaj do koszyka", "dodaj do", "kup teraz",
    "cena", "produkt", "produkty", "oferta", "hurtownia", "producent",
    "dystrybutor", "dostępny", "dostepny", "brutto", "add to cart",
    "shopping cart", "buy now", "price", "product", "in stock", "shop",
    "store", "wholesale", "manufacturer",
)
PRODUCT_PATH_TERMS = (
    "produkt", "products", "product", "oferta", "item", "p/", "sku",
    "katalog", "catalog", "towar", "shop",
)


def domain_of(url: str) -> str:
    return (urlparse(url).hostname or "").lower().removeprefix("www.")


def is_public_host(host: str) -> bool:
    if not host or host.lower() in ("localhost", "localhost.localdomain"):
        return False
    try:
        return ipaddress.ip_address(host).is_global
    except ValueError:
        try:
            addresses = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
            return bool(addresses) and all(
                ipaddress.ip_address(item[4][0]).is_global for item in addresses
            )
        except (OSError, ValueError):
            return False


def normalize_url(url: str) -> str:
    if not url:
        return ""
    url, _ = urldefrag(url.strip())
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return ""
    if parsed.username or parsed.password:
        return ""
    return url


def _normal_text(value: str) -> str:
    value = (value or "").lower().replace("ł", "l")
    value = re.sub(r"[^\w]+", " ", value, flags=re.UNICODE)
    return " ".join(value.split())


def query_tokens(query: str) -> list[str]:
    return [
        token for token in _normal_text(query).split()
        if len(token) >= 2 and token not in STOP_WORDS
    ]


def product_match_score(query: str, text: str) -> float:
    tokens = query_tokens(query)
    if not tokens:
        return 0.0
    haystack = f" {_normal_text(text)} "
    matched = sum(1 for token in tokens if f" {token} " in haystack)
    return matched / len(tokens)


def is_adult_content(url: str, title: str, text: str) -> bool:
    host = domain_of(url).replace(".", " ")
    sample = f" {host} {title} {text[:12000]} ".lower()
    return any(term in sample for term in ADULT_TERMS)


def classify_source(title: str, text: str) -> str:
    hay = (title + " " + text).lower()
    if any(x in hay for x in ("hurtownia", "wholesale", "dystrybutor")):
        return "hurtownia / dystrybutor"
    if any(x in hay for x in ("producent", "manufacturer", "factory")):
        return "producent"
    if any(x in hay for x in ("ogłoszenia", "ogloszenia", "classified", "aukcje")):
        return "ogłoszenia / marketplace"
    if any(x in hay for x in ("katalog produktów", "katalog produktow", "catalog")):
        return "katalog"
    if any(x in hay for x in ("sklep", "koszyk", "dodaj do koszyka", "shopping cart", "add to cart", "buy now")):
        return "sklep"
    return "strona / inne"


def site_search_urls(seed_urls, query):
    """Known first-party site-search adapters; no external search engines."""
    urls = []
    for raw in seed_urls:
        parsed = urlparse(raw)
        host = (parsed.hostname or "").lower().removeprefix("www.")
        if host == "rossmann.pl":
            urls.append("https://www.rossmann.pl/szukaj?" + urlencode({"Search": query}))
    return list(dict.fromkeys(urls))


def expand_queries(query: str) -> list[str]:
    q = " ".join(query.split())
    if not q:
        return []
    variants = [q, f'"{q}"', f"{q} cena", f"{q} sklep OR hurtownia", f"{q} producent"]
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
              currency TEXT, snippet TEXT, query TEXT, availability TEXT DEFAULT '',
              found_at TEXT DEFAULT CURRENT_TIMESTAMP);
            CREATE INDEX IF NOT EXISTS idx_offers_domain ON offers(domain);
            """)
            columns = {row[1] for row in db.execute("PRAGMA table_info(offers)")}
            if "availability" not in columns:
                db.execute("ALTER TABLE offers ADD COLUMN availability TEXT DEFAULT ''")

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
        if kind not in VALID_SOURCE_TYPES:
            return
        with self.connect() as db:
            db.execute(
                "INSERT INTO sources(domain,source_type) VALUES(?,?) "
                "ON CONFLICT(domain) DO UPDATE SET last_seen=CURRENT_TIMESTAMP,source_type=excluded.source_type",
                (domain, kind),
            )

    def save_page(self, url, domain, title, snippet, status="ok"):
        with self.connect() as db:
            db.execute(
                "INSERT INTO pages(url,domain,title,snippet,status) VALUES(?,?,?,?,?) "
                "ON CONFLICT(url) DO UPDATE SET title=excluded.title,snippet=excluded.snippet,"
                "last_checked=CURRENT_TIMESTAMP,status=excluded.status",
                (url, domain, title, snippet, status),
            )
            db.execute(
                "UPDATE sources SET pages_checked=pages_checked+1,last_seen=CURRENT_TIMESTAMP WHERE domain=?",
                (domain,),
            )

    def save_offer(self, url, domain, title, price, currency, snippet, query, availability=""):
        with self.connect() as db:
            db.execute(
                "INSERT INTO offers(url,domain,title,price,currency,snippet,query,availability) VALUES(?,?,?,?,?,?,?,?) "
                "ON CONFLICT(url) DO UPDATE SET title=excluded.title,price=excluded.price,"
                "currency=excluded.currency,snippet=excluded.snippet,query=excluded.query,"
                "availability=excluded.availability,found_at=CURRENT_TIMESTAMP",
                (url, domain, title, price, currency, snippet, query, availability),
            )

    def known_sources(self, limit=100):
        with self.connect() as db:
            return [
                dict(row) for row in db.execute(
                    "SELECT * FROM sources WHERE source_type IN ('hurtownia / dystrybutor', 'producent', 'ogłoszenia / marketplace', 'katalog', 'sklep') "
                    "ORDER BY last_seen DESC LIMIT ?", (limit,)
                )
            ]

    def offers_for_query(self, query, limit=100):
        tokens = query_tokens(query)
        with self.connect() as db:
            rows = [dict(row) for row in db.execute(
                "SELECT * FROM offers ORDER BY found_at DESC LIMIT 1000"
            )]
        if not tokens:
            return []
        ranked = []
        for row in rows:
            score = product_match_score(query, (row.get("title") or "") + " " + (row.get("snippet") or ""))
            if score >= 0.75:
                ranked.append((score, row))
        ranked.sort(key=lambda item: item[0], reverse=True)
        return [row for _, row in ranked[:limit]]


class PoliteCrawler:
    def __init__(self, db: SourceDB, max_pages=24, pages_per_domain=8):
        self.db = db
        self.max_pages = max_pages
        self.pages_per_domain = pages_per_domain
        self.session = requests.Session()
        self.session.headers.update(HEADERS)
        self.robots = {}
        self.last_request = {}
        self.host_public = {}
        self.errors = []
        self.pages_checked = 0
        self.pages_rejected = 0
        self.store_search_reports = []

    def allowed(self, url):
        parsed = urlparse(url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        if origin not in self.robots:
            parser = RobotFileParser()
            parser.set_url(urljoin(origin, "/robots.txt"))
            try:
                response = self.session.get(parser.url, timeout=5)
                if response.status_code == 200:
                    parser.parse(response.text.splitlines())
                elif response.status_code in (401, 403):
                    self.robots[origin] = None
                    return False
                else:
                    parser.parse(["User-agent: *", "Allow: /"])
            except requests.RequestException:
                self.robots[origin] = None
                return False
            self.robots[origin] = parser
        parser = self.robots[origin]
        return bool(parser and parser.can_fetch(USER_AGENT, url))

    def fetch(self, url):
        host = domain_of(url)
        if host not in self.host_public:
            self.host_public[host] = is_public_host(host)
        if not self.host_public[host]:
            self.errors.append(f"{host}: pominięto adres niepubliczny lub nierozwiązywalny")
            return None
        wait = 1.0 - (time.monotonic() - self.last_request.get(host, 0))
        if wait > 0:
            time.sleep(wait)
        self.last_request[host] = time.monotonic()
        if not self.allowed(url):
            return None
        try:
            response = self.session.get(url, timeout=TIMEOUT, stream=True, allow_redirects=False)
            if 300 <= response.status_code < 400:
                target = normalize_url(urljoin(url, response.headers.get("Location", "")))
                if not target or domain_of(target) != host or not self.allowed(target):
                    self.errors.append(f"{host}: pominięto przekierowanie poza domenę lub niedozwolone robots.txt")
                    return None
                response = self.session.get(target, timeout=TIMEOUT, stream=True, allow_redirects=False)
                if 300 <= response.status_code < 400:
                    self.errors.append(f"{host}: zbyt wiele przekierowań")
                    return None
            if response.status_code in (401, 403, 429):
                self.errors.append(f"{host}: HTTP {response.status_code}")
                return None
            response.raise_for_status()
            content_type = response.headers.get("Content-Type", "").lower()
            if not any(kind in content_type for kind in ("text/html", "application/xhtml", "xml")):
                return None
            chunks, size = [], 0
            for chunk in response.iter_content(65536):
                size += len(chunk)
                if size > MAX_BYTES:
                    break
                chunks.append(chunk)
            body = b"".join(chunks).decode(response.encoding or "utf-8", "replace")
            return response.url, body, content_type
        except requests.RequestException as exc:
            self.errors.append(f"{host}: {exc.__class__.__name__}")
            return None

    def sitemap_urls(self, base_url, cap=30):
        parsed = urlparse(base_url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        found = []
        try:
            response = self.session.get(urljoin(origin, "/robots.txt"), timeout=3)
            for line in response.text.splitlines():
                if line.lower().startswith("sitemap:"):
                    sitemap = line.split(":", 1)[1].strip()
                    if domain_of(sitemap) == domain_of(base_url):
                        found.append(sitemap)
        except requests.RequestException:
            return []
        page_urls = []
        for sitemap in found[:1]:
            try:
                response = self.session.get(sitemap, timeout=4)
                if response.status_code != 200:
                    continue
                root = ET.fromstring(response.content)
                for node in root.findall(".//{*}loc"):
                    target = normalize_url(node.text or "")
                    if target and domain_of(target) == domain_of(base_url):
                        page_urls.append(target)
                        if len(page_urls) >= cap:
                            return page_urls[:cap]
            except (requests.RequestException, ET.ParseError):
                continue
        return page_urls[:cap]

    def extract(self, url, html, query):
        soup = BeautifulSoup(html, "html.parser")
        architecture = detect_site_architecture(html, soup)
        product_meta = extract_product_metadata(soup)
        jsonld_nodes = [node.string or node.get_text() for node in soup.select('script[type="application/ld+json"]')]
        meta_product = product_meta["is_product"]
        for tag in soup(["script", "style", "noscript", "svg"]):
            tag.decompose()
        title = (soup.title.get_text(" ", strip=True) if soup.title else "")[:300]
        text = re.sub(r"\s+", " ", soup.get_text(" ", strip=True))
        snippet = text[:12000]
        product_names = [product_meta["name"]] if product_meta["name"] else []
        json_product = False
        structured_price = ""
        structured_currency = ""
        for raw in jsonld_nodes:
            try:
                parsed = json.loads(raw)
            except (ValueError, TypeError):
                continue
            stack = parsed if isinstance(parsed, list) else [parsed]
            while stack:
                obj = stack.pop()
                if isinstance(obj, dict):
                    types = obj.get("@type", [])
                    types = [types] if isinstance(types, str) else types
                    if any(kind in ("Product", "IndividualProduct", "ProductGroup") for kind in types):
                        json_product = True
                        if obj.get("name"):
                            product_names.append(str(obj["name"]))
                        offers = obj.get("offers", {})
                        offers = offers if isinstance(offers, list) else [offers]
                        for offer in offers:
                            if isinstance(offer, dict) and offer.get("price") is not None:
                                structured_price = str(offer.get("price"))
                                structured_currency = str(offer.get("priceCurrency", ""))
                                break
                    stack.extend(value for value in obj.values() if isinstance(value, (dict, list)))
                elif isinstance(obj, list):
                    stack.extend(obj)
        price_match = PRICE_RE.search(text)
        price = structured_price or product_meta["price"] or (price_match.group(1) if price_match else "")
        currency = structured_currency or product_meta["currency"] or (price_match.group(2).upper() if price_match else "")
        match_text = " ".join([title, " ".join(product_names), text[:12000]])
        relevance = product_match_score(query, match_text)
        commercial_text = text.lower()
        commerce_signal = (
            json_product or meta_product
            or any(term in commercial_text for term in COMMERCIAL_TERMS)
            or any(term in urlparse(url).path.lower() for term in PRODUCT_PATH_TERMS)
        )
        source_type = classify_source(title, text[:5000])
        if (json_product or meta_product) and source_type == "strona / inne":
            source_type = "sklep"
        commercial_source = commerce_signal and source_type in VALID_SOURCE_TYPES
        path = urlparse(url).path.lower()
        product_detail_url = any(
            marker in path for marker in ("/produkt/", "/product/", "/products/", "/p/")
        )
        # A shop homepage/category page is never a confirmed offer merely because
        # it contains a price and generic words such as "sklep" or "produkt".
        is_product_offer = (
            relevance >= 0.75 and bool(price)
            and (json_product or meta_product or product_detail_url)
        )
        links = []
        self._last_search_form_links = set()
        self._last_adapter_search_links = set()
        self._last_site_architecture = architecture
        # Detect public GET search forms on each shop instead of guessing that
        # every retailer uses the same query parameter. This mirrors entering
        # the user's phrase into the shop's own search box.
        current = urlparse(url)
        already_searching = bool(current.query) or any(
            marker in current.path.lower() for marker in ("/szukaj", "/search", "/wyniki")
        )
        if not already_searching:
            for form in soup.select("form"):
                method = (form.get("method") or "get").strip().lower()
                if method != "get":
                    continue
                action = normalize_url(urljoin(url, form.get("action") or url))
                if not action or domain_of(action) != domain_of(url):
                    continue
                # Reject embedded Google Custom Search forms even when the
                # shop proxies them through a same-host endpoint.
                form_blob = " ".join([
                    str(form.get("class", "")), str(form.get("id", "")),
                    str(form.get("action", "")),
                    " ".join((field.get("name", "") + " " + field.get("value", ""))
                             for field in form.select("input"))
                ]).lower()
                if ("google" in form_blob or "gcse" in form_blob
                        or "sitesearch" in form_blob
                        or re.search(r"client\s*=\s*pub-", form_blob)
                        or urlparse(action).path.lower().endswith("links2.php")):
                    continue
                search_field = None
                for field in form.select("input[name], textarea[name]"):
                    name = (field.get("name") or "").strip()
                    kind = (field.get("type") or "").lower()
                    if kind == "hidden":
                        continue
                    if kind == "search" or name.lower() in {
                        "q", "query", "search", "s", "term", "keyword",
                        "phrase", "text", "searchterm", "search_query",
                    }:
                        search_field = name
                        break
                if not search_field:
                    continue
                params = {}
                for field in form.select("input[name]"):
                    name = (field.get("name") or "").strip()
                    kind = (field.get("type") or "").lower()
                    if not name or kind in {"submit", "button", "file", "password"}:
                        continue
                    if kind == "hidden" and any(
                        secret in name.lower() for secret in ("token", "csrf", "auth", "session")
                    ):
                        continue
                    value = field.get("value", "")
                    if kind in {"checkbox", "radio"} and not field.has_attr("checked"):
                        continue
                    params[name] = query if name == search_field else value
                for field in form.select("textarea[name]"):
                    if field.get("name") == search_field:
                        params[search_field] = query
                if search_field not in params:
                    params[search_field] = query
                search_url = action
                from urllib.parse import parse_qsl, urlunparse
                parsed_action = urlparse(action)
                merged_params = dict(parse_qsl(parsed_action.query, keep_blank_values=True))
                merged_params.update(params)
                search_url = urlunparse(parsed_action._replace(query=urlencode(merged_params, doseq=True)))
                if search_url != url:
                    links.append((search_url, "internal shop search"))
                    self._last_search_form_links.add(search_url)
        # Platform adapters are selected from page fingerprints, not domain names.
        # They complement (never replace) a detected first-party GET search form.
        for adapter_url in platform_search_urls(url, query, architecture):
            if domain_of(adapter_url) == domain_of(url) and adapter_url != url:
                links.append((adapter_url, "platform adapter search"))
                self._last_adapter_search_links.add(adapter_url)
        for anchor in soup.select("a[href]"):
            target = normalize_url(urljoin(url, anchor.get("href", "")))
            if not target or target == url or domain_of(target) != domain_of(url):
                continue
            path = urlparse(target).path.lower()
            if any(part in path for part in (
                "/login", "/konto", "/cart", "/koszyk", "/checkout",
                "/polityka", "/privacy", "/regulamin", "/terms",
            )):
                continue
            anchor_text = anchor.get_text(" ", strip=True)
            links.append((target, anchor_text))
        unique = {}
        for target, anchor_text in links:
            unique[target] = max(
                unique.get(target, ("", -1)),
                (anchor_text, product_match_score(query, anchor_text + " " + target)),
                key=lambda item: item[1],
            )
        ranked_links = sorted(
            unique.items(),
            key=lambda item: (
                1 if item[1][0] in {"internal shop search", "platform adapter search"} else 0,
                item[1][1] + (0.25 if any(term in urlparse(item[0]).path.lower() for term in PRODUCT_PATH_TERMS) else 0),
                len(urlparse(item[0]).path),
            ),
            reverse=True,
        )
        return title, snippet, (is_product_offer, price, currency), [item[0] for item in ranked_links[:100]], source_type, commercial_source, relevance

    def crawl(self, query, seed_urls, deep=False):
        max_pages = self.max_pages * (2 if deep else 1)
        per_domain = self.pages_per_domain * (2 if deep else 1)
        queue = deque()
        seen = set()
        for raw in seed_urls:
            seed = normalize_url(raw)
            if seed:
                queue.append(seed)
        domain_counts = {}
        results = []
        store_result_urls = set()
        while queue and len(seen) < max_pages:
            url = queue.popleft()
            if url in seen:
                continue
            seen.add(url)
            domain = domain_of(url)
            if not domain or domain_counts.get(domain, 0) >= per_domain:
                continue
            fetched = self.fetch(url)
            if not fetched:
                continue
            final_url, html, content_type = fetched
            if "xml" in content_type:
                try:
                    root = ET.fromstring(html)
                    for node in root.findall(".//{*}loc"):
                        target = normalize_url(node.text or "")
                        if target and domain_of(target) == domain and target not in seen:
                            queue.append(target)
                except ET.ParseError:
                    pass
                continue
            self.pages_checked += 1
            title, snippet, offer_data, links, source_type, commercial_source, relevance = self.extract(final_url, html, query)
            # The shop search engine is authoritative: count and read only
            # its explicit product cards. Product detail pages never create,
            # validate, or overwrite search results.
            page_soup = BeautifulSoup(html, "html.parser")
            store_rows = extract_store_search_results(page_soup, final_url, query)
            parsed_page = urlparse(final_url)
            from urllib.parse import parse_qs
            search_page = (
                bool(re.search(r"/(?:search|szukaj|wyniki|catalogsearch)(?:/|$)", parsed_page.path, re.I))
                or bool(set(parse_qs(parsed_page.query)) &
                        {"q", "s", "search", "query", "text", "searchterm", "phrase"})
            )
            if search_page:
                page_text = re.sub(r"\s+", " ", page_soup.get_text(" ", strip=True))
                total_count = next((row.get("store_total_count") for row in store_rows
                                    if row.get("store_total_count") is not None), None)
                if total_count is None:
                    for count_pattern in (
                        r"(?:znaleziono|wyszukano|wyników|wyniki|products found|results found)\D{0,30}(\d{1,6})",
                        r"(\d{1,6})\s+(?:produktów|produkt|products|wyników|results)",
                    ):
                        count_match = re.search(count_pattern, page_text, re.I)
                        if count_match:
                            total_count = int(count_match.group(1))
                            break
                no_results = bool(re.search(
                    r"(nie znaleziono (?:żadnych )?(?:produktów|wyników)|brak wyników|no products found|no results)",
                    page_text, re.I))
                self.store_search_reports.append({
                    "domain": domain, "search_url": final_url,
                    "returned_cards": len(store_rows),
                    "store_reported_total": total_count,
                    "product_exists": (total_count > 0) if total_count is not None
                                      else (True if store_rows else (False if no_results else None)),
                    "status": ("found" if store_rows or (total_count is not None and total_count > 0)
                               else ("not_found" if no_results or total_count == 0 else "unknown")),
                })
            for store_row in store_rows:
                self.db.save_offer(
                    store_row["url"], domain, store_row["title"],
                    store_row["price"], store_row["currency"],
                    store_row["snippet"], query, store_row["availability"],
                )
                results.append({
                    **store_row, "domain": domain,
                    "source": "Wynik wyszukiwarki sklepu",
                    "relevance": 1.0,
                })
            if is_adult_content(final_url, title, snippet):
                self.pages_rejected += 1
                continue
            if commercial_source:
                self.db.save_source(domain, source_type)
            is_offer, price, currency = offer_data
            if commercial_source and relevance >= 0.5:
                # Keep a relevant commercial product/category page, not arbitrary pages.
                self.db.save_page(final_url, domain, title, snippet, status="product_candidate")
            domain_counts[domain] = domain_counts.get(domain, 0) + 1
            if domain_counts[domain] == 1:
                for sitemap_url in reversed(self.sitemap_urls(final_url, cap=60 if deep else 30)):
                    if sitemap_url not in seen:
                        queue.appendleft(sitemap_url)
            # A first-party search results page is the most valuable entry point:
            # visit its matching product detail links before generic seed/sitemap
            # URLs, otherwise the global page budget can be consumed first.
            path_lower = urlparse(final_url).path.lower()
            if domain == "rossmann.pl" and path_lower.startswith("/szukaj"):
                priority_links = [
                    link for link in links
                    if "/produkt/" in urlparse(link).path.lower()
                ]
                for link in reversed(priority_links):
                    if link not in seen and len(queue) < max_pages * 8:
                        queue.appendleft(link)
                priority_set = set(priority_links)
                for link in links:
                    if link not in priority_set and link not in seen and len(queue) < max_pages * 8:
                        queue.append(link)
            else:
                # Visit a detected first-party search form immediately. It is
                # the closest equivalent to a real user searching inside the shop.
                search_links = [
                    link for link in links
                    if link in (
                        getattr(self, "_last_search_form_links", set())
                        | getattr(self, "_last_adapter_search_links", set())
                    )
                ]
                for link in reversed(search_links):
                    if link not in seen and len(queue) < max_pages * 8:
                        queue.appendleft(link)
                for link in links:
                    if link not in search_links and link not in seen and len(queue) < max_pages * 8:
                        queue.append(link)
        return results


def load_seed_catalog():
    """Bootstrap list for independent crawling; not a third-party search index."""
    catalog_path = Path(__file__).with_name("seed_catalog.json")
    try:
        data = json.loads(catalog_path.read_text(encoding="utf-8"))
        return [url for url in data.get("seeds", []) if normalize_url(url)]
    except (OSError, ValueError, TypeError):
        return []


def discover(query: str, deep=False, db_path=None, extra_seeds=None):
    """Search public pages using our crawler, sitemaps and local SQLite index only."""
    db = SourceDB(db_path)
    seed_urls = list(load_seed_catalog())
    for raw in (extra_seeds or []):
        seed = normalize_url(raw)
        if seed:
            seed_urls.append(seed)
    for source in db.known_sources(100):
        if source.get("domain"):
            seed_urls.append("https://" + source["domain"] + "/")
    seed_urls = list(dict.fromkeys(seed_urls))
    # Search directly inside supported shops first, then crawl their public
    # product links. This remains first-party crawling, not a search engine.
    direct_searches = site_search_urls(seed_urls, query)
    seed_urls = list(dict.fromkeys(direct_searches + seed_urls))
    crawler = PoliteCrawler(db, max_pages=60 if deep else 30,
                            pages_per_domain=12 if deep else 6)
    fresh = crawler.crawl(query, seed_urls, deep=deep) if seed_urls else []
    # Search output is sourced from current first-party store result pages.
    # Older crawler-inferred database rows are deliberately not mixed in.
    merged = {row["url"]: row for row in fresh}
    ranked = sorted(merged.values(),
                    key=lambda row: (row.get("relevance", 0), bool(row.get("price")),
                                     row.get("title", "")), reverse=True)
    return {
        "results": ranked[:150],
        "sources": db.known_sources(200),
        "pages_checked": crawler.pages_checked,
        "pages_rejected": crawler.pages_rejected,
        "errors": crawler.errors[:30],
        "seed_count": len(seed_urls),
        "direct_site_searches": direct_searches,
        "store_search_reports": crawler.store_search_reports,
        "engine_independent": True,
    }
