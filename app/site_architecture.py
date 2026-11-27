"""Adaptive public storefront architecture detection and search adapters.

Adapters are evidence-based: a platform is selected only when its fingerprints
are present. Generic GET forms remain the preferred source of search parameters.
No POST forms, private APIs, login flows, CAPTCHA or anti-bot bypasses.
"""
from __future__ import annotations

import re
from urllib.parse import quote, urlencode, urljoin, urlparse, urlunparse, parse_qs


PLATFORM_FINGERPRINTS = {
    "woocommerce": (
        re.compile(r"/wp-content/(?:plugins/woocommerce|themes)/", re.I),
        re.compile(r"woocommerce", re.I),
        re.compile(r"wc-block|woocommerce-page", re.I),
    ),
    "prestashop": (
        re.compile(r"prestashop", re.I),
        re.compile(r"/modules/[^/]+/views/", re.I),
        re.compile(r"prestashop-", re.I),
    ),
    "magento": (
        re.compile(r"magento", re.I),
        re.compile(r"mage/|requirejs-config", re.I),
        re.compile(r"catalogsearch", re.I),
    ),
    "shopify": (
        re.compile(r"cdn\.shopify\.com", re.I),
        re.compile(r"Shopify\.theme|shopify-section", re.I),
        re.compile(r"myshopify\.com", re.I),
    ),
    "opencart": (
        re.compile(r"index\.php\?route=|route=common/home", re.I),
        re.compile(r"catalog/view/theme|opencart", re.I),
    ),
    "shoper": (
        re.compile(r"shoper", re.I),
        re.compile(r"shoplo|shoper-", re.I),
    ),
    "idosell": (
        re.compile(r"idosell|iai_shop", re.I),
        re.compile(r"iai.shop", re.I),
    ),
}


def detect_site_architecture(html: str, soup=None) -> dict:
    """Return a conservative platform guess and evidence, never a hard-coded domain."""
    sample = html or ""
    if soup is not None:
        sample += " " + " ".join(
            str(value) for tag in soup.select("script[src],link[href]")
            for value in (tag.get("src"), tag.get("href")) if value
        )
        sample += " " + " ".join(str(x) for x in soup.get("class", [])) if hasattr(soup, "get") else ""
    scores = {}
    evidence = {}
    for platform, patterns in PLATFORM_FINGERPRINTS.items():
        hits = [pattern.pattern for pattern in patterns if pattern.search(sample)]
        if hits:
            scores[platform] = len(hits)
            evidence[platform] = hits
    if not scores:
        return {"platform": "custom_or_unknown", "confidence": 0.0, "evidence": []}
    platform = max(scores, key=scores.get)
    confidence = min(0.99, 0.45 + 0.18 * scores[platform])
    return {"platform": platform, "confidence": round(confidence, 2),
            "evidence": evidence[platform]}


def platform_search_urls(url: str, query: str, architecture: dict) -> list[str]:
    """Build public search routes only for positively fingerprinted shop engines."""
    if not query.strip():
        return []
    platform = architecture.get("platform")
    parsed = urlparse(url)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    encoded = query.strip()
    candidates = {
        "woocommerce": origin + "/?" + urlencode({"s": encoded, "post_type": "product"}),
        "prestashop": origin + "/szukaj?" + urlencode({"controller": "search", "s": encoded}),
        "magento": origin + "/catalogsearch/result/?" + urlencode({"q": encoded}),
        "shopify": origin + "/search?" + urlencode({"q": encoded}),
        "opencart": origin + "/index.php?" + urlencode({"route": "product/search", "search": encoded}),
        "shoper": origin + "/searchquery/" + quote(encoded, safe="") + "/",
        "idosell": origin + "/search.php?" + urlencode({"text": encoded}),
    }
    candidate = candidates.get(platform)
    return [candidate] if candidate else []


def extract_product_metadata(soup) -> dict:
    """Read common product metadata beyond JSON-LD (OpenGraph, Twitter, microdata)."""
    def meta(*selectors):
        for selector in selectors:
            tag = soup.select_one(selector)
            if tag:
                value = tag.get("content") or tag.get("value") or tag.get_text(" ", strip=True)
                if value:
                    return str(value).strip()
        return ""

    name = meta('meta[property="og:title"]', 'meta[name="twitter:title"]',
                '[itemprop="name"]')
    price = meta('meta[property="product:price:amount"]',
                 'meta[property="og:price:amount"]',
                 'meta[itemprop="price"]', '[itemprop="price"]')
    currency = meta('meta[property="product:price:currency"]',
                    'meta[property="og:price:currency"]',
                    'meta[itemprop="priceCurrency"]', '[itemprop="priceCurrency"]')
    availability = meta('meta[property="product:availability"]',
                        '[itemprop="availability"]')
    product_type = meta('meta[property="og:type"]')
    has_product_microdata = bool(
        soup.select_one('[itemtype*="schema.org/Product" i]')
        or soup.select_one('[itemprop="offers"]')
    )
    return {
        "name": name, "price": price, "currency": currency,
        "availability": availability,
        "is_product": product_type.lower() == "product" or has_product_microdata,
    }


def extract_store_search_summary(soup) -> dict:
    """Extract only an explicitly labelled store result count / empty state."""
    text = re.sub(r"\s+", " ", soup.get_text(" ", strip=True))
    count_scopes = soup.select(
        "[class*='result-count'], [class*='results-count'], "
        "[class*='product-count'], [class*='search-count'], "
        "[class*='toolbar-amount'], [class*='pagination-info'], "
        "[data-total-count], [data-results-count]"
    )
    scoped_text = " ".join(
        " ".join([
            node.get("data-total-count", ""),
            node.get("data-results-count", ""),
            node.get_text(" ", strip=True),
        ]) for node in count_scopes
    )
    patterns = (
        re.compile(r"\b(?:wyświetlono|wyswietlono|pokazano)\s+\d+\s*[-–]\s*\d+\s+(?:z|of)\s+(\d{1,6})\b", re.I),
        re.compile(r"\b(?:znaleziono|wyszukano|liczba\s+wyników|liczba\s+produktów|results?\s+found|products?\s+found)\D{0,35}(\d{1,6})\b", re.I),
        re.compile(r"\b(\d{1,6})\s+(?:produktów|produkt|products|results|wyników)\b", re.I),
        re.compile(r"\b(?:total|data-total-count|data-results-count)\D{0,12}(\d{1,6})\b", re.I),
    )
    total = None
    for source in (scoped_text, text):
        for pattern in patterns:
            match = pattern.search(source)
            if match:
                total = int(match.group(1))
                break
        if total is not None:
            break
    no_results = bool(re.search(
        r"\b(?:nie znaleziono (?:żadnych )?(?:produktów|wyników)|"
        r"brak wyników|brak produkt(?:u|ów)|no products found|no results)\b",
        text, re.I
    ))
    if total == 0:
        no_results = True
    return {"store_total_count": total, "store_reported_no_results": no_results}


def extract_store_search_results(soup, page_url: str, query: str) -> list[dict]:
    """Compatibility adapter: use the platform-independent adaptive DOM engine."""
    from app.adaptive_search import inspect_search_page

    parsed = urlparse(page_url)
    params = {key.lower(): values for key, values in parse_qs(parsed.query).items()}
    search_path = bool(re.search(
        r"/(?:search|szukaj|wyniki|catalogsearch)(?:/|$)", parsed.path, re.I
    ))
    has_search_param = any(key in params for key in
                           ("q", "s", "search", "query", "text", "searchterm", "phrase"))
    if not (search_path or has_search_param):
        return []

    report = inspect_search_page(soup, page_url, query)
    legacy_summary = extract_store_search_summary(soup)
    total_count = report["store_total_count"]
    if total_count is None:
        total_count = legacy_summary["store_total_count"]
    no_results = report["store_reported_no_results"] or legacy_summary["store_reported_no_results"]
    if total_count == 0 or no_results:
        return []

    rows = report["cards"]
    for row in rows:
        row.update({
            "store_total_count": total_count,
            "store_returned_cards": len(rows),
            "store_product_exists": (total_count > 0) if total_count is not None else True,
            "store_reported_no_results": no_results,
        })
    return rows
