"""Adaptive public storefront architecture detection and search adapters.

Adapters are evidence-based: a platform is selected only when its fingerprints
are present. Generic GET forms remain the preferred source of search parameters.
No POST forms, private APIs, login flows, CAPTCHA or anti-bot bypasses.
"""
from __future__ import annotations

import re
from urllib.parse import quote, urlencode, urlparse, urlunparse


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
