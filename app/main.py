import sys
import re
import base64
import xml.etree.ElementTree as ET
import concurrent.futures
import time
from urllib.parse import quote_plus, urlparse, parse_qs, unquote
import webbrowser

try:
    from app.discovery import discover
except ModuleNotFoundError:
    from discovery import discover

import requests
from bs4 import BeautifulSoup
from PySide6.QtCore import QThread, Signal, Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QComboBox, QTableWidget,
    QTableWidgetItem, QHeaderView, QMessageBox, QProgressBar,
    QAbstractItemView, QMenu, QTabWidget, QTextBrowser, QGroupBox,
    QFormLayout
)

APP_NAME = "Przeglądarka"
APP_VERSION = "0.3.1"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Accept": "text/html,application/rss+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "pl-PL,pl;q=0.9,en;q=0.8",
}
TIMEOUT = 14
ENGINE_NAMES = [
    "Google", "Bing", "Brave Search", "Mojeek", "DuckDuckGo",
    "Qwant", "Ecosia", "Startpage", "Yahoo",
]
PROVIDER_DOMAINS = {
    "Google": ("google.", "googleusercontent.com"),
    "Bing": ("bing.com", "microsoft.com"),
    "Brave Search": ("search.brave.com", "brave.com"),
    "Mojeek": ("mojeek.com",),
    "DuckDuckGo": ("duckduckgo.com",),
    "Qwant": ("qwant.com",),
    "Ecosia": ("ecosia.org",),
    "Startpage": ("startpage.com",),
    "Yahoo": ("yahoo.com", "search.yahoo."),
}


def clean_url(url):
    if not url:
        return ""
    url = unquote(url.strip())
    if url.startswith("//"):
        url = "https:" + url
    host = domain_of(url)
    if "bing.com/ck/a" in url:
        try:
            encoded = parse_qs(urlparse(url).query).get("u", [""])[0]
            if encoded.startswith("a1"):
                payload = encoded[2:]
                payload += "=" * (-len(payload) % 4)
                decoded = base64.urlsafe_b64decode(payload).decode("utf-8", "ignore")
                if decoded.startswith(("http://", "https://")):
                    url = decoded
        except Exception:
            pass
    if "duckduckgo.com/l/?" in url:
        try:
            url = parse_qs(urlparse(url).query).get("uddg", [url])[0]
        except Exception:
            pass
    if "google.com/url" in url:
        try:
            url = parse_qs(urlparse(url).query).get("q", [url])[0]
        except Exception:
            pass
    if not url.startswith(("http://", "https://")):
        return ""
    return url.split("#", 1)[0]


def domain_of(url):
    try:
        host = urlparse(url).netloc.lower().split("@")[-1].split(":")[0]
        return host[4:] if host.startswith("www.") else host
    except Exception:
        return ""


def is_provider_url(url, engine):
    host = domain_of(url)
    return any(part in host for part in PROVIDER_DOMAINS.get(engine, ()))


def _result(title, url, snippet, engine):
    title = re.sub(r"\s+", " ", title or "").strip()
    url = clean_url(url)
    if not title or not url or is_provider_url(url, engine):
        return None
    return {
        "title": title[:300],
        "url": url,
        "snippet": re.sub(r"\s+", " ", snippet or "").strip()[:600],
        "engine": engine,
    }


def parse_bing_rss(xml_content):
    """Parse Bing RSS search results."""
    try:
        root = ET.fromstring(xml_content)
    except ET.ParseError:
        return []
    found = []
    for item in root.findall(".//item"):
        title_node = item.find("title")
        link_node = item.find("link")
        desc_node = item.find("description")
        title = title_node.text.strip() if title_node is not None and title_node.text else ""
        target = link_node.text.strip() if link_node is not None and link_node.text else ""
        description = ""
        if desc_node is not None and desc_node.text:
            description = BeautifulSoup(desc_node.text, "html.parser").get_text(" ", strip=True)
        row = _result(title, target, description, "Bing")
        if row:
            found.append(row)
    return found


def parse_search_html(html, engine):
    """Extract organic-looking result links from common public SERP HTML layouts."""
    soup = BeautifulSoup(html, "html.parser")
    found, seen = [], set()

    selectors = {
        "Google": ("div.MjjYud a:has(h3)", "a:has(h3)", "div.g a:has(h3)"),
        "Brave Search": ('a[data-testid="result-title"]', 'a[data-testid="result-title-a"]',
                         ".snippet a[href]", "main h2 a[href]", "main h3 a[href]"),
        "Mojeek": ("a.ob[href]", "a.result-title[href]", ".results-standard h2 a[href]",
                   "main h2 a[href]", "main h3 a[href]"),
        "DuckDuckGo": ("a.result-link[href]", "a.result__a[href]"),
        "Qwant": (".webResult a[href]", ".result a[href]", "main h2 a[href]", "main h3 a[href]"),
        "Ecosia": (".result a[href]", "a.result__title[href]", "main h2 a[href]", "main h3 a[href]"),
        "Startpage": (".w-gl__result-title[href]", ".result a[href]", "main h2 a[href]",
                      "main h3 a[href]"),
        "Yahoo": (".dd.algo h3 a[href]", "h3 a[href]", "main h2 a[href]"),
    }
    links = []
    for selector in selectors.get(engine, ("h2 a[href]", "h3 a[href]")):
        try:
            links.extend(soup.select(selector))
        except Exception:
            continue

    for link in links:
        target = clean_url(link.get("href", ""))
        if not target or is_provider_url(target, engine) or target in seen:
            continue
        title = link.get_text(" ", strip=True)
        if not title:
            heading = link.find(["h2", "h3"])
            title = heading.get_text(" ", strip=True) if heading else ""
        container = link
        for _ in range(5):
            if container.parent is None:
                break
            container = container.parent
            if container.name in ("li", "article"):
                break
        snippet_node = container.select_one(
            ".VwiC3b, .IsZvec, .snippet-description, .result__snippet, "
            ".content, .description, p"
        )
        snippet = snippet_node.get_text(" ", strip=True) if snippet_node else ""
        row = _result(title, target, snippet, engine)
        if row:
            seen.add(target)
            found.append(row)

    # Some providers return simple HTML cards without h2/h3 wrappers.
    if not found and engine in ("Brave Search", "Qwant", "Ecosia", "Startpage"):
        for link in soup.select("a[href]"):
            target = clean_url(link.get("href", ""))
            title = link.get_text(" ", strip=True)
            if not target or len(title) < 8 or is_provider_url(target, engine):
                continue
            if target in seen:
                continue
            row = _result(title, target, "", engine)
            if row:
                seen.add(target)
                found.append(row)
            if len(found) >= 20:
                break

    if not found:
        text = soup.get_text(" ", strip=True).lower()
        if any(word in text for word in (
            "captcha", "unusual traffic", "automated requests", "verify you are",
            "enable javascript", "access denied", "robot check"
        )):
            raise RuntimeError(f"{engine}: strona zabezpieczyła automatyczne zapytanie.")
        raise RuntimeError(f"{engine}: odpowiedź odebrana, ale nie rozpoznano wyników.")
    return found


def _fetch_html(engine, url, query):
    response = requests.get(url, params={"q": query}, headers=HEADERS, timeout=TIMEOUT)
    response.raise_for_status()
    return parse_search_html(response.text, engine)


def search_google(query):
    return _fetch_html("Google", "https://www.google.com/search", query)


def search_brave(query):
    return _fetch_html("Brave Search", "https://search.brave.com/search", query)


def search_mojeek(query):
    return _fetch_html("Mojeek", "https://www.mojeek.com/search", query)


def search_duckduckgo(query):
    errors = []
    for url in ("https://lite.duckduckgo.com/lite/",
                "https://html.duckduckgo.com/html/"):
        try:
            response = requests.get(url, params={"q": query}, headers=HEADERS, timeout=TIMEOUT)
            response.raise_for_status()
            try:
                results = parse_search_html(response.text, "DuckDuckGo")
                if results:
                    return results
            except Exception as exc:
                errors.append(str(exc))
        except Exception as exc:
            errors.append(str(exc))
    raise RuntimeError(errors[-1] if errors else "DuckDuckGo nie zwrócił wyników.")


def search_qwant(query):
    return _fetch_html("Qwant", "https://www.qwant.com/", query)


def search_ecosia(query):
    return _fetch_html("Ecosia", "https://www.ecosia.org/search", query)


def search_startpage(query):
    return _fetch_html("Startpage", "https://www.startpage.com/sp/search", query)


def search_yahoo(query):
    return _fetch_html("Yahoo", "https://search.yahoo.com/search", query)


def search_bing(query):
    rss_url = "https://www.bing.com/search"
    rss_error = None
    try:
        response = requests.get(
            rss_url, params={"format": "rss", "q": query},
            headers=HEADERS, timeout=TIMEOUT
        )
        response.raise_for_status()
        results = parse_bing_rss(response.content)
        if results:
            return results
    except Exception as exc:
        rss_error = exc
    try:
        response = requests.get(
            rss_url, params={"q": query}, headers=HEADERS, timeout=TIMEOUT
        )
        response.raise_for_status()
        return parse_search_html(response.text, "Bing")
    except Exception as exc:
        detail = f" (RSS: {rss_error})" if rss_error else ""
        raise RuntimeError(f"Bing: {exc}{detail}") from exc


SEARCHERS = {
    "Google": search_google,
    "Bing": search_bing,
    "Brave Search": search_brave,
    "Mojeek": search_mojeek,
    "DuckDuckGo": search_duckduckgo,
    "Qwant": search_qwant,
    "Ecosia": search_ecosia,
    "Startpage": search_startpage,
    "Yahoo": search_yahoo,
}


def merge_results(results):
    """Deduplicate URLs while retaining every engine that found each page."""
    merged, by_url = [], {}
    for item in results:
        key = item["url"].rstrip("/").lower()
        if key in by_url:
            existing = by_url[key]
            names = existing["engine"].split(", ")
            if item["engine"] not in names:
                existing["engine"] = ", ".join(names + [item["engine"]])
            if not existing["snippet"] and item.get("snippet"):
                existing["snippet"] = item["snippet"]
        else:
            copied = dict(item)
            by_url[key] = copied
            merged.append(copied)
    return merged


class SearchWorker(QThread):
    results_ready = Signal(list, list)

    def __init__(self, query, engines):
        super().__init__()
        self.query = query
        self.engines = engines

    def run(self):
        results, errors = [], []
        with concurrent.futures.ThreadPoolExecutor(max_workers=min(9, len(self.engines) or 1)) as pool:
            future_map = {
                pool.submit(SEARCHERS[name], self.query): name for name in self.engines
            }
            for future in concurrent.futures.as_completed(future_map):
                name = future_map[future]
                try:
                    results.extend(future.result())
                except Exception as exc:
                    errors.append(f"{name}: {exc}")
        self.results_ready.emit(merge_results(results), errors)


class DiscoveryWorker(QThread):
    finished_data = Signal(dict)
    def __init__(self, query, deep, seeds=None):
        super().__init__()
        self.query, self.deep, self.seeds = query, deep, seeds or []
    def run(self):
        try:
            data = discover(self.query, deep=self.deep, extra_seeds=self.seeds)
        except Exception as exc:
            data = {"results": [], "sources": [], "pages_checked": 0, "seed_count": 0, "errors": [f"Błąd modułu odkrywania: {exc}"]}
        self.finished_data.emit(data)

class DiagnosticsWorker(QThread):
    diagnostics_ready = Signal(list)

    def run(self):
        rows = []
        started = time.monotonic()
        try:
            response = requests.get(
                "https://www.msftconnecttest.com/connecttest.txt",
                headers=HEADERS, timeout=8
            )
            response.raise_for_status()
            elapsed = time.monotonic() - started
            rows.append(("Internet (HTTPS)", "Działa", 1, f"{elapsed:.1f} s",
                         "Połączenie wychodzące HTTPS jest dostępne"))
        except Exception as exc:
            elapsed = time.monotonic() - started
            rows.append(("Internet (HTTPS)", "Brak potwierdzenia", 0, f"{elapsed:.1f} s",
                         str(exc)[:220]))
        for name in ENGINE_NAMES:
            started = time.monotonic()
            try:
                results = SEARCHERS[name]("sprężarka Embraco EMT")
                elapsed = time.monotonic() - started
                rows.append((name, "Działa", len(results), f"{elapsed:.1f} s", "Otrzymano wyniki"))
            except Exception as exc:
                elapsed = time.monotonic() - started
                rows.append((name, "Niedostępna", 0, f"{elapsed:.1f} s", str(exc)[:220]))
        self.diagnostics_ready.emit(rows)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"Przeglądarka {APP_VERSION} — niezależne wyszukiwanie")
        self.resize(1180, 760)
        self.setMinimumSize(820, 580)
        self.worker = None
        self.diagnostics_worker = None
        self.discovery_worker = None
        self.all_results = []
        self._build_ui()

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(18, 14, 18, 14)
        root.setSpacing(10)

        title = QLabel("PRZEGLĄDARKA")
        title.setFont(QFont("Segoe UI", 22, QFont.Bold))
        title.setStyleSheet("color:#183b56;")
        root.addWidget(title)
        subtitle = QLabel("Niezależne wyszukiwanie w wielu publicznych źródłach internetu.")
        subtitle.setStyleSheet("color:#536575;font-size:13px;")
        root.addWidget(subtitle)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_search_tab(), "Wyszukiwanie")
        self.tabs.addTab(self._build_discovery_tab(), "Odkrywanie ofert")
        self.tabs.addTab(self._build_diagnostics_tab(), "Diagnostyka połączenia")
        self.tabs.addTab(self._build_about_tab(), "Przejrzystość programu")
        root.addWidget(self.tabs, 1)
        self.setStyleSheet(
            "QMainWindow{background:#f7f9fa;} QLineEdit,QComboBox,QPushButton{"
            "border:1px solid #cbd7de;border-radius:6px;padding:7px;background:white;}"
            "QPushButton:hover{background:#edf3f6;} QTabWidget::pane{border:1px solid #d5e0e6;"
            "background:white;border-radius:6px;} QTabBar::tab{padding:9px 16px;}"
        )

    def _build_search_tab(self):
        tab = QWidget()
        root = QVBoxLayout(tab)
        root.setContentsMargins(10, 12, 10, 10)
        root.setSpacing(10)

        search_row = QHBoxLayout()
        self.query = QLineEdit()
        self.query.setPlaceholderText("Wpisz produkt, część lub temat, np. sprężarka Embraco EMT")
        self.query.setMinimumHeight(42)
        self.query.setClearButtonEnabled(True)
        self.query.returnPressed.connect(self.start_search)
        self.provider = QComboBox()
        self.provider.addItems(["Wszystkie źródła"] + ENGINE_NAMES)
        self.provider.setMinimumHeight(42)
        self.search_button = QPushButton("Szukaj")
        self.search_button.setMinimumHeight(42)
        self.search_button.setMinimumWidth(110)
        self.search_button.setStyleSheet(
            "QPushButton{background:#176b87;color:white;border:0;border-radius:6px;"
            "padding:8px 18px;font-weight:bold;} QPushButton:hover{background:#12566d;}"
            "QPushButton:disabled{background:#9aabb4;}"
        )
        self.search_button.clicked.connect(self.start_search)
        search_row.addWidget(self.query, 1)
        search_row.addWidget(self.provider)
        search_row.addWidget(self.search_button)
        root.addLayout(search_row)

        info_row = QHBoxLayout()
        self.status = QLabel("Wpisz zapytanie i rozpocznij wyszukiwanie.")
        self.status.setStyleSheet("color:#536575;")
        self.count = QLabel("0 wyników")
        self.count.setStyleSheet("font-weight:bold;color:#183b56;")
        info_row.addWidget(self.status, 1)
        info_row.addWidget(self.count)
        root.addLayout(info_row)

        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(5)
        self.progress.setRange(0, 0)
        self.progress.hide()
        root.addWidget(self.progress)

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Tytuł / źródło", "Opis", "Znalazły wyszukiwarki", "Domena"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        self.table.cellDoubleClicked.connect(self.open_selected)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self.show_context_menu)
        root.addWidget(self.table, 1)

        bottom = QHBoxLayout()
        self.open_button = QPushButton("Otwórz zaznaczony wynik")
        self.open_button.clicked.connect(self.open_current)
        self.copy_button = QPushButton("Kopiuj adres")
        self.copy_button.clicked.connect(self.copy_current)
        note = QLabel("Dwuklik otwiera stronę w domyślnej przeglądarce.")
        note.setStyleSheet("color:#71808a;")
        bottom.addWidget(self.open_button)
        bottom.addWidget(self.copy_button)
        bottom.addStretch(1)
        bottom.addWidget(note)
        root.addLayout(bottom)
        return tab

    def _build_discovery_tab(self):
        tab = QWidget()
        root = QVBoxLayout(tab)
        root.setContentsMargins(12, 12, 12, 12)
        intro = QLabel("Moduł odkrywa publiczne strony sklepów, hurtowni i producentów. Analizuje podstrony, mapy witryn i odnośniki, a znalezione domeny oraz oferty zapisuje lokalnie w bazie SQLite.")
        intro.setWordWrap(True)
        root.addWidget(intro)
        row = QHBoxLayout()
        self.discovery_query = QLineEdit()
        self.discovery_query.setPlaceholderText("Np. sprężarka Embraco EMT, pompa obiegowa 25-60")
        self.discovery_query.setMinimumHeight(40)
        self.discovery_query.returnPressed.connect(self.run_discovery)
        self.discovery_depth = QComboBox()
        self.discovery_depth.addItems(["Szybkie odkrywanie", "Głębokie odkrywanie"])
        self.discovery_button = QPushButton("Odkrywaj strony i oferty")
        self.discovery_button.clicked.connect(self.run_discovery)
        row.addWidget(self.discovery_query, 1)
        row.addWidget(self.discovery_depth)
        row.addWidget(self.discovery_button)
        root.addLayout(row)
        seed_row = QHBoxLayout()
        seed_label = QLabel("Dodatkowe adresy startowe:")
        self.discovery_seeds = QLineEdit()
        self.discovery_seeds.setPlaceholderText("Opcjonalnie: https://hurtownia.pl/; https://producent.eu/")
        seed_row.addWidget(seed_label)
        seed_row.addWidget(self.discovery_seeds, 1)
        root.addLayout(seed_row)
        self.discovery_status = QLabel("Baza źródeł jest przechowywana lokalnie na tym komputerze. Możesz rozpocząć skanowanie również bez wyszukiwarki, podając własne adresy startowe.")
        root.addWidget(self.discovery_status)
        self.discovery_table = QTableWidget(0, 5)
        self.discovery_table.setHorizontalHeaderLabels(["Produkt / strona", "Cena", "Domena", "Opis", "Adres"])
        for col in (0, 1, 2, 4): self.discovery_table.horizontalHeader().setSectionResizeMode(col, QHeaderView.ResizeToContents)
        self.discovery_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        self.discovery_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.discovery_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.discovery_table.setAlternatingRowColors(True)
        self.discovery_table.verticalHeader().setVisible(False)
        self.discovery_table.cellDoubleClicked.connect(self.open_discovery_result)
        root.addWidget(self.discovery_table, 1)
        self.discovery_summary = QLabel("Crawler respektuje robots.txt, limity serwerów i nie omija logowania ani CAPTCHA.")
        self.discovery_summary.setWordWrap(True)
        root.addWidget(self.discovery_summary)
        return tab

    def _build_diagnostics_tab(self):
        tab = QWidget()
        root = QVBoxLayout(tab)
        root.setContentsMargins(12, 12, 12, 12)
        intro = QLabel(
            "Test sprawdza rzeczywistą odpowiedź każdego źródła na przykładowe zapytanie. "
            "Wynik zależy od internetu, regionu, limitów i zabezpieczeń dostawcy."
        )
        intro.setWordWrap(True)
        root.addWidget(intro)
        self.diag_button = QPushButton("Sprawdź połączenie ze wszystkimi źródłami")
        self.diag_button.clicked.connect(self.run_diagnostics)
        root.addWidget(self.diag_button)
        self.diag_summary = QLabel("Diagnostyka nie została jeszcze uruchomiona.")
        self.diag_summary.setStyleSheet("font-weight:bold;color:#183b56;padding:4px;")
        root.addWidget(self.diag_summary)
        self.diag_table = QTableWidget(0, 5)
        self.diag_table.setHorizontalHeaderLabels(
            ["Źródło", "Status", "Wyniki", "Czas", "Szczegóły"]
        )
        self.diag_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.diag_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.diag_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.diag_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self.diag_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.Stretch)
        self.diag_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.diag_table.setAlternatingRowColors(True)
        self.diag_table.verticalHeader().setVisible(False)
        root.addWidget(self.diag_table, 1)
        hint = QLabel(
            "Uwaga: status „Niedostępna” nie musi oznaczać awarii internetu. "
            "Wyszukiwarka może blokować automatyczne zapytania lub zmienić stronę."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color:#687984;")
        root.addWidget(hint)
        return tab

    def _build_about_tab(self):
        tab = QWidget()
        root = QVBoxLayout(tab)
        root.setContentsMargins(12, 12, 12, 12)
        info = QTextBrowser()
        info.setOpenExternalLinks(True)
        info.setHtml("""
        <h2>Jak działa Przeglądarka</h2>
        <p><b>Wersja:</b> 0.3.1</p>
        <p>Przeglądarka jest narzędziem do wyszukiwania i porządkowania publicznie
        dostępnych wyników. Nie jest sklepem ani platformą sprzedażową.</p>
        <h3>Połączenia internetowe</h3>
        <ul>
          <li>Program wykonuje wyłącznie połączenia wychodzące HTTPS do wybranych
          wyszukiwarek, aby wysłać zapytanie i odebrać wyniki.</li>
          <li>Nie uruchamia serwera na komputerze i nie wymaga otwierania portów
          na routerze ani przyjmowania połączeń z internetu.</li>
          <li>Zapytanie jest przekazywane wybranym dostawcom wyszukiwania. Dostawcy
          mogą przetwarzać je zgodnie z własnymi zasadami prywatności.</li>
          <li>Otwieranie znalezionych stron odbywa się w domyślnej przeglądarce Windows.</li>
        </ul>
        <h3>Niezależne odkrywanie stron</h3><ul><li>Wyszukiwarki są tylko jednym ze sposobów znalezienia pierwszych adresów startowych.</li><li>Następnie program sam analizuje publiczne podstrony, linki i mapy witryn oraz zapisuje domeny i oferty w lokalnej bazie SQLite.</li><li>Przy kolejnych zapytaniach może ponownie wykorzystać wcześniej odkryte domeny.</li><li>Wykrycie ceny lub słów handlowych nie potwierdza dostępności ani wiarygodności sprzedawcy.</li></ul><h3>Wyniki i ranking</h3>
        <ul>
          <li>Program nie sprzedaje miejsc w wynikach i nie pobiera opłat od sprzedawców.</li>
          <li>Wyniki są zbierane z wybranych źródeł i łączone po adresie strony.</li>
          <li>Wskazanie kilku wyszukiwarek oznacza, że każda z nich zwróciła ten adres;
          nie jest to ocena jakości ani potwierdzenie niezależności ich indeksów.</li>
          <li>Niektóre wyszukiwarki korzystają z cudzych indeksów lub syndykują wyniki,
          dlatego źródła nie zawsze są od siebie niezależne.</li>
        </ul>
        <h3>Ograniczenia i bezpieczeństwo</h3>
        <ul>
          <li>Nie gwarantujemy kompletności internetu ani aktualności ofert.</li>
          <li>Automatyczne pobieranie może być ograniczane przez dostawców, limity,
          CAPTCHA lub zmiany układu stron.</li>
          <li>Program nie omija logowania, CAPTCHA ani zabezpieczeń antybotowych.</li>
          <li>Nie wykonuje płatności, nie zamawia produktów i nie wysyła danych
          do sprzedawców poza otwarciem wybranego adresu w przeglądarce.</li>
        </ul>
        <p><b>Podpis cyfrowy:</b> plik instalacyjny wymaga certyfikatu wydawcy.
        Podpis zostanie dodany do kompilacji po skonfigurowaniu certyfikatu
        Authenticode. Brak podpisu nie jest ukrywany przez program.</p>
        """)
        root.addWidget(info, 1)
        return tab

    def start_search(self):
        query = self.query.text().strip()
        if not query:
            QMessageBox.information(self, "Brak zapytania", "Wpisz nazwę produktu, części lub temat.")
            self.query.setFocus()
            return
        selected = self.provider.currentText()
        engines = ENGINE_NAMES if selected == "Wszystkie źródła" else [selected]
        self.search_button.setEnabled(False)
        self.progress.show()
        self.status.setText("Przeszukuję wybrane źródła równolegle…")
        self.count.setText("Wyszukiwanie…")
        self.table.setRowCount(0)
        self.worker = SearchWorker(query, engines)
        self.worker.results_ready.connect(self.show_results)
        self.worker.start()

    def show_results(self, results, errors):
        self.all_results = results
        self.table.setRowCount(len(results))
        for row, result in enumerate(results):
            title = QTableWidgetItem(result["title"])
            title.setToolTip(result["url"])
            title.setData(Qt.UserRole, result["url"])
            self.table.setItem(row, 0, title)
            self.table.setItem(row, 1, QTableWidgetItem(result["snippet"]))
            self.table.setItem(row, 2, QTableWidgetItem(result["engine"]))
            self.table.setItem(row, 3, QTableWidgetItem(domain_of(result["url"])))
        self.search_button.setEnabled(True)
        self.progress.hide()
        self.count.setText(f"{len(results)} wyników")
        if errors and not results:
            self.status.setText("Żadne wybrane źródło nie zwróciło wyników.")
            QMessageBox.warning(
                self, "Problem z wyszukiwaniem",
                "Nie udało się pobrać wyników:\n\n" + "\n".join(errors[:6]) +
                "\n\nOtwórz zakładkę Diagnostyka połączenia, aby sprawdzić źródła."
            )
        elif errors:
            self.status.setText(
                f"Pokazuję dostępne wyniki. Niedostępne źródła: {len(errors)}. "
                "Szczegóły w diagnostyce."
            )
        elif results:
            self.status.setText(
                "Wyniki połączono i usunięto duplikaty. Program nie promuje sprzedawców."
            )
        else:
            self.status.setText("Brak wyników dla tego zapytania.")

    def run_discovery(self):
        query = self.discovery_query.text().strip()
        if not query:
            QMessageBox.information(self, "Brak zapytania", "Wpisz nazwę produktu lub części.")
            self.discovery_query.setFocus()
            return
        if self.discovery_worker and self.discovery_worker.isRunning(): return
        self.discovery_button.setEnabled(False)
        self.discovery_status.setText("Odkrywam publiczne strony, sprawdzam mapy witryn i zapisuję źródła…")
        self.discovery_table.setRowCount(0)
        seeds = [x.strip() for x in re.split(r'[;\n,]+', self.discovery_seeds.text()) if x.strip()]
        self.discovery_worker = DiscoveryWorker(query, self.discovery_depth.currentIndex() == 1, seeds)
        self.discovery_worker.finished_data.connect(self.show_discovery_results)
        self.discovery_worker.start()

    def show_discovery_results(self, data):
        rows = data.get("results", [])
        self.discovery_table.setRowCount(len(rows))
        for row, item in enumerate(rows):
            title = QTableWidgetItem(item.get("title", ""))
            title.setData(Qt.UserRole, item.get("url", ""))
            self.discovery_table.setItem(row, 0, title)
            price = " ".join(x for x in (item.get("price", ""), item.get("currency", "")) if x)
            self.discovery_table.setItem(row, 1, QTableWidgetItem(price or "Nie wykryto"))
            self.discovery_table.setItem(row, 2, QTableWidgetItem(item.get("domain", "")))
            self.discovery_table.setItem(row, 3, QTableWidgetItem(item.get("snippet", "")))
            self.discovery_table.setItem(row, 4, QTableWidgetItem(item.get("url", "")))
        self.discovery_button.setEnabled(True)
        self.discovery_status.setText(f"Sprawdzone strony: {data.get('pages_checked', 0)} | znane domeny: {len(data.get('sources', []))} | adresy startowe: {data.get('seed_count', 0)} | wykryte oferty: {len(rows)}")
        errors = data.get("errors", [])
        self.discovery_summary.setText(("Uwagi: " + "; ".join(errors[:5])) if errors else "Skanowanie zakończone. Wyniki i źródła zapisano w lokalnej bazie.")

    def open_discovery_result(self, row, column):
        item = self.discovery_table.item(row, 0)
        if item and item.data(Qt.UserRole): webbrowser.open(item.data(Qt.UserRole))

    def run_diagnostics(self):
        if self.diagnostics_worker and self.diagnostics_worker.isRunning():
            return
        self.diag_button.setEnabled(False)
        self.diag_summary.setText("Testuję źródła — może to potrwać około 1–2 minut…")
        self.diag_table.setRowCount(0)
        self.diagnostics_worker = DiagnosticsWorker()
        self.diagnostics_worker.diagnostics_ready.connect(self.show_diagnostics)
        self.diagnostics_worker.start()

    def show_diagnostics(self, rows):
        self.diag_table.setRowCount(len(rows))
        ok = 0
        internet_ok = False
        for row, data in enumerate(rows):
            for col, value in enumerate(data):
                self.diag_table.setItem(row, col, QTableWidgetItem(str(value)))
            if data[0] == "Internet (HTTPS)":
                internet_ok = data[1] == "Działa"
            elif data[1] == "Działa":
                ok += 1
        self.diag_button.setEnabled(True)
        internet_text = "Internet HTTPS: działa" if internet_ok else "Internet HTTPS: brak potwierdzenia"
        self.diag_summary.setText(
            f"{internet_text}. Wyszukiwarki: {ok} z {len(ENGINE_NAMES)} zwróciło wyniki. "
            "To test bieżącej dostępności, nie gwarancja przyszłego działania."
        )

    def selected_url(self):
        row = self.table.currentRow()
        if row < 0:
            return ""
        item = self.table.item(row, 0)
        return item.data(Qt.UserRole) if item else ""

    def open_selected(self, row, column):
        item = self.table.item(row, 0)
        if item:
            webbrowser.open(item.data(Qt.UserRole))

    def open_current(self):
        url = self.selected_url()
        if url:
            webbrowser.open(url)

    def copy_current(self):
        url = self.selected_url()
        if url:
            QApplication.clipboard().setText(url)
            self.status.setText("Adres skopiowany do schowka.")

    def show_context_menu(self, point):
        row = self.table.rowAt(point.y())
        if row < 0:
            return
        self.table.selectRow(row)
        menu = QMenu(self)
        open_action = menu.addAction("Otwórz stronę")
        copy_action = menu.addAction("Kopiuj adres")
        chosen = menu.exec(self.table.viewport().mapToGlobal(point))
        if chosen == open_action:
            self.open_current()
        elif chosen == copy_action:
            self.copy_current()


def main():
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setFont(QFont("Segoe UI", 10))
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
