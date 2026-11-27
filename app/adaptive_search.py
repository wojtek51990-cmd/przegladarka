"""Adaptive first-party shop search engine.

The engine does not depend on shop names, platform fingerprints, URL conventions,
or CSS class names. It discovers public search controls and infers product cards
from repeated DOM structure plus product-specific evidence.

Safety: same-origin search forms only; no credentials, checkout/account forms,
CAPTCHA bypass, private APIs, or external search engines. POST is supported only
for forms positively identified as search forms.
"""
from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse


SEARCH_WORDS = re.compile(
    r"(search|szukaj|wyszuk|query|keyword|phrase|term)",
    re.I,
)
SENSITIVE = re.compile(r"(csrf|nonce|token|auth|password|passwd|session|cookie|login|account)", re.I)
PRICE_WORDS = re.compile(r"(price|cena|cost|amount|regular-price|special-price|sale-price)", re.I)
STOCK_WORDS = re.compile(r"(stock|availability|available|dostep|dostęp|inventory)", re.I)
CARD_WORDS = re.compile(r"(product|produkt|item|tile|card|result|listing|offer|towar)", re.I)
PRICE_RE = re.compile(
    r"(?<![\w])([0-9][0-9 .]*(?:,[0-9]{2}|\.[0-9]{2})?)\s*(zł|PLN|EUR|€|USD|\$)",
    re.I,
)
STOCK_RE = re.compile(
    r"(dostępny(?:\s+od\s+ręki)?|dostepny(?:\s+od\s+reki)?|"
    r"brak\s+w\s+magazynie|niedostępny|niedostepny|na\s+zamówienie|"
    r"na\s+zamowienie|w\s+magazynie|in\s+stock|out\s+of\s+stock|"
    r"available|unavailable)",
    re.I,
)
SEARCH_NAMES = {
    "q", "query", "search", "s", "term", "keyword", "phrase", "text",
    "searchterm", "search_query", "string",
}


@dataclass(frozen=True)
class SearchRequest:
    method: str
    url: str
    data: tuple[tuple[str, str], ...]
    field: str
    confidence: float
    evidence: tuple[str, ...]

    def payload(self, query: str) -> dict[str, str]:
        result = dict(self.data)
        result[self.field] = query
        return result


def _origin(url: str) -> tuple[str, str, int | None]:
    parsed = urlparse(url)
    return parsed.scheme.lower(), (parsed.hostname or "").lower(), parsed.port


def _is_search_form(form) -> tuple[str, float, list[str]]:
    evidence: list[str] = []
    candidates = []
    for field in form.select("input[name], textarea[name]"):
        name = (field.get("name") or "").strip()
        kind = (field.get("type") or "text").lower()
        if not name or kind in {"hidden", "password", "file", "submit", "button"}:
            continue
        label_text = ""
        field_id = field.get("id")
        if field_id:
            label = form.find("label", attrs={"for": field_id})
            if label:
                label_text = label.get_text(" ", strip=True)
        blob = " ".join((name, kind, field.get("placeholder", ""),
                         field.get("aria-label", ""), label_text))
        score = 0
        if kind == "search":
            score += 5
            evidence.append("input[type=search]")
        if name.lower() in SEARCH_NAMES:
            score += 4
            evidence.append("rozpoznana nazwa pola")
        if SEARCH_WORDS.search(blob):
            score += 2
            evidence.append("etykieta/pole wskazuje wyszukiwanie")
        if score:
            candidates.append((score, name))
    if not candidates:
        return "", 0.0, []
    candidates.sort(reverse=True)
    score, field_name = candidates[0]
    confidence = min(0.99, 0.55 + score * 0.07)
    return field_name, confidence, list(dict.fromkeys(evidence))


def discover_search_requests(html_soup, page_url: str) -> list[SearchRequest]:
    """Discover same-origin public GET/POST search forms without platform rules."""
    requests: list[SearchRequest] = []
    base_origin = _origin(page_url)
    for form in html_soup.select("form"):
        method = (form.get("method") or "get").strip().lower()
        if method not in {"get", "post"}:
            continue
        action = urljoin(page_url, form.get("action") or page_url)
        parsed = urlparse(action)
        if parsed.scheme not in {"http", "https"} or _origin(action) != base_origin:
            continue
        form_blob = " ".join([
            str(form.get("id", "")), " ".join(form.get("class", [])),
            str(form.get("action", "")),
            form.get_text(" ", strip=True)[:500],
        ]).lower()
        control_names = {
            str(control.get("name", "")).lower()
            for control in form.select("[name]")
        }
        # Reject embedded third-party search widgets even when their form is
        # technically hosted on the shop domain (e.g. Google Custom Search).
        external_search_signature = (
            bool(control_names.intersection({"sitesearch", "cx", "gcse"}))
            or ("client" in control_names and bool(control_names.intersection({"forid", "cof", "domains"})))
            or bool(re.search(r"(google|gcse|sitesearch)", form_blob))
        )
        if external_search_signature or re.search(
            r"(captcha|checkout|koszyk|login|logowanie)", form_blob
        ):
            continue
        field, confidence, evidence = _is_search_form(form)
        if not field:
            continue
        payload: list[tuple[str, str]] = []
        for control in form.select("input[name], textarea[name], select[name]"):
            name = (control.get("name") or "").strip()
            kind = (control.get("type") or "").lower()
            if not name or SENSITIVE.search(name) or kind in {"password", "file", "submit", "button", "image"}:
                continue
            if kind in {"checkbox", "radio"} and not control.has_attr("checked"):
                continue
            if control.name == "select":
                selected = control.select_one("option[selected]") or control.select_one("option")
                value = selected.get("value", "") if selected else ""
            elif control.name == "textarea":
                value = control.get_text()
            else:
                value = control.get("value", "")
            if name == field:
                value = "__QUERY__"
            payload.append((name, str(value)))
        if not any(name == field for name, _ in payload):
            payload.append((field, "__QUERY__"))
        # A POST form is accepted only when the query field itself is positively
        # identified; arbitrary POST forms are never replayed.
        if method == "post":
            evidence.append("formularz POST z jednoznacznym polem wyszukiwania")
        else:
            evidence.append("formularz GET z jednoznacznym polem wyszukiwania")
        requests.append(SearchRequest(
            method=method.upper(), url=urlunparse(parsed._replace(fragment="")),
            data=tuple(payload), field=field, confidence=confidence,
            evidence=tuple(dict.fromkeys(evidence)),
        ))
    requests.sort(key=lambda item: (item.confidence, item.method == "GET"), reverse=True)
    return requests


def _node_signature(node) -> tuple[str, tuple[str, ...]]:
    classes = tuple(sorted(str(x).lower() for x in node.get("class", []) if x))
    return node.name or "", classes


def _same_origin_link(anchor, page_url: str) -> str:
    href = (anchor.get("href") or "").strip()
    if not href or href.startswith(("#", "javascript:", "mailto:", "tel:")):
        return ""
    target = urljoin(page_url, href)
    parsed = urlparse(target)
    if parsed.scheme not in {"http", "https"} or _origin(target) != _origin(page_url):
        return ""
    if parsed.path.rstrip("/") == urlparse(page_url).path.rstrip("/") and parsed.query == urlparse(page_url).query:
        return ""
    return urlunparse(parsed._replace(fragment=""))


def _price_from_card(card) -> tuple[str, str]:
    # First preference: semantic price fields inside this exact card.
    for node in card.select(
        '[itemprop="price"], [data-price], [class*="price"], [id*="price"], '
        '[class*="cena"], [id*="cena"], meta[property*="price"]'
    ):
        raw = node.get("content") or node.get("data-price") or node.get("value") or node.get_text(" ", strip=True)
        raw = re.sub(r"\s+", " ", str(raw)).strip()
        if not raw:
            continue
        currency = "PLN" if re.search(r"(zł|PLN)", raw, re.I) else ""
        match = PRICE_RE.search(raw)
        if match:
            return match.group(1).replace(" ", ""), match.group(2).upper()
        if re.fullmatch(r"[0-9]+(?:[,.][0-9]{1,2})?", raw) and currency:
            return raw, currency
    # Fallback is deliberately limited to the card's own text, never page text.
    match = PRICE_RE.search(card.get_text(" ", strip=True))
    return (match.group(1).replace(" ", ""), match.group(2).upper()) if match else ("", "")


def _card_evidence(node, anchor, sibling_repeats: int) -> tuple[int, list[str]]:
    score, reasons = 0, []
    if node.get("itemtype") and "product" in str(node.get("itemtype")).lower():
        score += 5; reasons.append("schema-product")
    if any(node.has_attr(attr) for attr in ("data-product", "data-product-id", "data-sku", "data-productid")):
        score += 4; reasons.append("product-data-attribute")
    if any(CARD_WORDS.search(str(value)) for value in node.get("class", [])) or CARD_WORDS.search(str(node.get("id", ""))):
        score += 1; reasons.append("semantic-container-name")
    if sibling_repeats >= 2:
        score += 3; reasons.append("repeated-sibling-structure")
    if node.select_one('[itemprop="price"], [itemprop="offers"], [class*="price"], [class*="cena"], [data-price]'):
        score += 2; reasons.append("price-field")
    if node.select_one('[itemprop="availability"], [class*="stock"], [class*="availability"], [class*="dostep"]'):
        score += 1; reasons.append("availability-field")
    if node.select_one("img"):
        score += 1; reasons.append("product-image")
    if node.select_one("h1, h2, h3, h4, [itemprop='name']"):
        score += 1; reasons.append("title-heading")
    if re.search(r"(add to cart|dodaj do koszyka|kup teraz)", node.get_text(" ", strip=True), re.I):
        score += 1; reasons.append("purchase-action")
    return score, reasons


def _extract_json_product_records(soup, page_url: str) -> list[dict]:
    """Read explicit product objects from public embedded JSON application state."""
    rows, seen = [], set()
    scripts = soup.select(
        'script[type="application/ld+json"], script[type="application/json"], '
        'script#__NEXT_DATA__, script[id*="state" i], script[id*="data" i]'
    )
    def walk(value):
        if isinstance(value, dict):
            yield value
            for child in value.values():
                yield from walk(child)
        elif isinstance(value, list):
            for child in value:
                yield from walk(child)
    for script in scripts:
        raw = script.string or script.get_text()
        if not raw or len(raw) > 2_000_000:
            continue
        try:
            payload = json.loads(raw)
        except (ValueError, TypeError):
            continue
        for obj in walk(payload):
            name = obj.get("name") or obj.get("title") or obj.get("productName")
            raw_url = obj.get("url") or obj.get("productUrl") or obj.get("link")
            if not isinstance(name, str) or not isinstance(raw_url, str):
                continue
            target = _same_origin_link(type("Link", (), {"get": lambda self, key, default=None: raw_url if key == "href" else default})(), page_url)
            if not target or target in seen or len(name.strip()) < 3 or len(name) > 300:
                continue
            types = obj.get("@type", [])
            types = [types] if isinstance(types, str) else types
            has_product_identity = (
                any("product" in str(item).lower() for item in types)
                or any(key in obj for key in ("sku", "productId", "product_id", "variantId"))
                or any(key in obj for key in ("price", "offers", "availability", "stock"))
            )
            if not has_product_identity:
                continue
            price_value = obj.get("price")
            currency = str(obj.get("priceCurrency") or obj.get("currency") or "")
            if isinstance(price_value, dict):
                currency = currency or str(price_value.get("currency") or "")
                price_value = price_value.get("value") or price_value.get("amount")
            availability = obj.get("availability") or obj.get("stock") or ""
            rows.append({
                "title": name.strip(), "url": target,
                "price": str(price_value) if price_value is not None else "",
                "currency": currency.upper(),
                "availability": str(availability),
                "snippet": json.dumps(obj, ensure_ascii=False)[:500],
                "card_confidence": 0.96, "card_evidence": ["embedded-product-json"],
            })
            seen.add(target)
    return rows


def extract_adaptive_result_cards(soup, page_url: str) -> list[dict]:
    """Infer result cards from DOM repetition and local product evidence.

    No product URL pattern or known shop CSS class is required. A card must
    have a same-origin link and sufficient independent structural evidence.
    """
    rows, seen = [], set()
    anchors = soup.select("a[href]")
    for anchor in anchors:
        target = _same_origin_link(anchor, page_url)
        if not target or target in seen:
            continue
        title = re.sub(r"\s+", " ", anchor.get_text(" ", strip=True)).strip()
        if not title or len(title) > 300 or len(title) < 2:
            continue
        candidates = []
        node = anchor
        for depth in range(1, 8):
            node = node.parent
            if node is None or getattr(node, "name", None) in {None, "body", "html"}:
                break
            parent = node.parent
            repeats = 0
            if parent:
                signature = _node_signature(node)
                repeats = sum(
                    1 for sibling in parent.find_all(node.name, recursive=False)
                    if _node_signature(sibling) == signature
                    and sibling.select_one("a[href]")
                )
            score, reasons = _card_evidence(node, anchor, repeats)
            # A custom wrapper can be identified by repetition even when it
            # has no familiar product class or product URL.
            if score >= 4 and (repeats >= 2 or score >= 5):
                links = {
                    _same_origin_link(link, page_url)
                    for link in node.select("a[href]")
                } - {""}
                if len(links) <= 3 and target in links:
                    candidates.append((score, -depth, node, reasons))
        if not candidates:
            continue
        _, _, card, reasons = max(candidates, key=lambda item: item[:2])
        heading = card.select_one("h1, h2, h3, h4, [itemprop='name']")
        if len(title) < 4 and heading:
            title = re.sub(r"\s+", " ", heading.get_text(" ", strip=True)).strip()
        if not title:
            continue
        card_text = re.sub(r"\s+", " ", card.get_text(" ", strip=True)).strip()
        price, currency = _price_from_card(card)
        stock = STOCK_RE.search(card_text)
        rows.append({
            "title": title, "url": target, "price": price, "currency": currency,
            "availability": stock.group(0) if stock else "",
            "snippet": card_text[:500],
            "card_confidence": min(0.99, 0.45 + len(set(reasons)) * 0.07),
            "card_evidence": reasons,
        })
        seen.add(target)
    # Embedded product records supplement DOM cards; they are still scoped to
    # the store's own returned search document and must carry product identity.
    for row in _extract_json_product_records(soup, page_url):
        if row["url"] not in seen:
            rows.append(row)
            seen.add(row["url"])
    return rows


def _query_matches_title(title: str, query: str) -> bool:
    """Reject unrelated catalogue cards when a shop ignores the search term.

    Match against the card title only, never the whole page or surrounding
    navigation. Prefer distinctive query terms; short generic words are used
    only when the query contains no longer token.
    """
    def normalize(value: str) -> str:
        value = unicodedata.normalize("NFKD", value or "")
        value = "".join(ch for ch in value if not unicodedata.combining(ch))
        return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()

    wanted = normalize(query).split()
    if not wanted:
        return True
    distinctive = [token for token in wanted if len(token) >= 5]
    terms = distinctive or wanted
    haystack = f" {normalize(title)} "
    return any(f" {token} " in haystack for token in terms)


def inspect_search_page(soup, page_url: str, query: str = "") -> dict:
    """Return machine-readable evidence about a first-party search response."""
    raw_cards = extract_adaptive_result_cards(soup, page_url)
    cards = [row for row in raw_cards if _query_matches_title(row.get("title", ""), query)]
    text = re.sub(r"\s+", " ", soup.get_text(" ", strip=True))
    empty = bool(re.search(
        r"(no results|no products found|brak wyników|nie znaleziono (?:żadnych )?(?:produktów|wyników))",
        text, re.I,
    ))
    count = None
    for node in soup.select(
        "[data-total-count], [data-results-count], [class*='result-count'], "
        "[class*='results-count'], [class*='product-count'], [class*='toolbar-amount']"
    ):
        raw = " ".join((str(node.get("data-total-count", "")),
                        str(node.get("data-results-count", "")),
                        node.get_text(" ", strip=True)))
        match = re.search(r"(?:of|z|znaleziono|wyników|produktów)?\s*(\d{1,7})\s*(?:produktów|produkt|wyników|results|products)?\b", raw, re.I)
        if match:
            count = int(match.group(1))
            break
    return {
        "cards": cards, "returned_cards": len(cards), "candidate_cards": len(raw_cards),
        "rejected_nonmatching_cards": len(raw_cards) - len(cards),
        "store_total_count": count,
        "store_reported_no_results": empty or count == 0,
        "has_product_evidence": bool(cards),
    }



def has_dynamic_search_evidence(soup) -> bool:
    """Avoid launching Chromium on ordinary pages with no search UI evidence."""
    if soup.select_one(
        'input[type="search"], input[placeholder*="szukaj" i], '
        'input[aria-label*="search" i], [role="search"], '
        'button[aria-label*="search" i], button[aria-label*="szukaj" i]'
    ):
        return True
    for script in soup.select("script[src]"):
        src = str(script.get("src", ""))
        if re.search(r"(search|szukaj|catalog|product)", src, re.I):
            return True
    for script in soup.select("script:not([src])"):
        raw = script.string or script.get_text()
        if raw and re.search(r"(searchProducts|searchProductsUrl|searchEndpoint|szukajProdukt|searchForm)", raw, re.I):
            return True
    return False


def render_store_search_page(url: str, query: str, timeout_ms: int = 15000) -> tuple[str, str] | None:
    """Optional embedded Chromium fallback for JavaScript-driven shop search.

    Requires Playwright plus its Chromium runtime. It does not solve CAPTCHAs,
    log in, evade bot checks, or navigate to another origin.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return None

    original_origin = _origin(url)
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(
                locale="pl-PL",
                user_agent="PrzegladarkaIndependentCrawler/0.6 (+public-web; respectful crawler)",
            )
            page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            page.wait_for_timeout(500)
            # If this is already a search URL, render the returned result state.
            parsed = urlparse(page.url)
            already_search = bool(
                re.search(r"/(?:search|szukaj|wyniki|catalogsearch)(?:/|$)", parsed.path, re.I)
                or any(k.lower() in {"q", "query", "search", "s", "term", "text"}
                       for k, _ in parse_qsl(parsed.query))
            )
            if not already_search:
                selectors = (
                    'input[type="search"]:visible',
                    'input[name="q"]:visible',
                    'input[name="query"]:visible',
                    'input[name*="search" i]:visible',
                    'input[placeholder*="szukaj" i]:visible',
                    'input[aria-label*="search" i]:visible',
                )
                search_box = None
                for selector in selectors:
                    locator = page.locator(selector).first
                    if locator.count():
                        search_box = locator
                        break
                if search_box is None:
                    browser.close()
                    return None
                search_box.fill(query, timeout=3000)
                search_box.press("Enter", timeout=3000)
                try:
                    page.wait_for_load_state("domcontentloaded", timeout=5000)
                except Exception:
                    pass
                page.wait_for_timeout(1200)
            if _origin(page.url) != original_origin:
                browser.close()
                return None
            body_text = page.locator("body").inner_text(timeout=3000)[:4000].lower()
            if any(marker in body_text for marker in (
                "captcha", "verify you are human", "access denied",
                "robot check", "zabezpieczenie antybotowe",
            )):
                browser.close()
                return None
            result = (page.url, page.content())
            browser.close()
            return result
    except Exception:
        return None
