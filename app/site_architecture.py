"""Adaptive public storefront architecture detection and search adapters.

Adapters are evidence-based: a platform is selected only when its fingerprints
are present. Generic GET forms remain the preferred source of search parameters.
No POST forms, private APIs, login flows, CAPTCHA or anti-bot bypasses.
"""
from __future__ import annotations

import re
from urllib.parse import quote, urlencode, urljoin, urlparse, urlunparse


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


def extract_store_search_results(soup, page_url: str, query: str) -> list[dict]:
    """Read cards returned by a store's own search page; never infer stock."""
    from urllib.parse import urlparse, parse_qs
    parsed = urlparse(page_url)
    params = {key.lower() for key in parse_qs(parsed.query)}
    search_path = any(part in parsed.path.lower() for part in
                      ("/search", "/szukaj", "/wyniki", "/catalogsearch"))
    if not (search_path or params & {"q", "s", "search", "query", "text", "searchterm"}):
        return []
    price_re = re.compile(
        r"(?<!\w)(\d{1,3}(?:[ .]\d{3})*(?:,\d{2})?|\d+(?:[,.]\d{2})?)\s?(zł|PLN|EUR|€|USD|\$)", re.I)
    stock_re = re.compile(
        r"(dostępny(?:\s+od\s+ręki)?|dostepny(?:\s+od\s+reki)?|brak\s+w\s+magazynie|"
        r"niedostępny|niedostepny|na\s+zamówienie|na\s+zamowienie|w\s+magazynie|"
        r"in\s+stock|out\s+of\s+stock|available|unavailable)", re.I)
    results, seen = [], set()
    product_href = re.compile(r"/(produkt|product|products|p|item)(/|[-_])", re.I)
    for anchor in soup.select("a[href]"):
        href = (anchor.get("href") or "").strip()
        if not href or href.startswith(("#", "javascript:", "mailto:")):
            continue
        target = urljoin(page_url, href)
        parsed_target = urlparse(target)
        if parsed_target.scheme not in ("http", "https") or parsed_target.netloc.lower() != parsed.netloc.lower():
            continue
        if target in seen or target.rstrip("/") == page_url.rstrip("/"):
            continue
        node, card = anchor.parent, None
        for _ in range(5):
            if node is None:
                break
            classes = " ".join(node.get("class", [])) if hasattr(node, "get") else ""
            node_id = node.get("id", "") if hasattr(node, "get") else ""
            if (node.name in ("article", "li") or node.has_attr("data-product")
                    or node.has_attr("data-product-id")
                    or re.search(r"product|item|card", classes + " " + node_id, re.I)):
                card = node
                break
            node = node.parent
        if card is None and not product_href.search(parsed_target.path):
            continue
        card = card or anchor.parent
        card_text = re.sub(r"\s+", " ", card.get_text(" ", strip=True)).strip()
        title = anchor.get_text(" ", strip=True)
        if not title:
            heading = card.select_one("h1, h2, h3, h4, [class*='title'], [class*='name']")
            title = heading.get_text(" ", strip=True) if heading else ""
        if not title or len(title) > 300:
            continue
        price_match, stock_match = price_re.search(card_text), stock_re.search(card_text)
        results.append({
            "title": title, "url": target,
            "price": price_match.group(1) if price_match else "",
            "currency": price_match.group(2).upper() if price_match else "",
            "availability": stock_match.group(0) if stock_match else "",
            "snippet": card_text[:500],
        })
        seen.add(target)
        if len(results) >= 80:
            break
    return results
