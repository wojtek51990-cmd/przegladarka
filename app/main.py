import sys
import re
import time
from urllib.parse import urlparse
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
APP_VERSION = "0.5.0"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "pl-PL,pl;q=0.9,en;q=0.8",
}
TIMEOUT = 14
def domain_of(url):
    try:
        host = urlparse(url).netloc.lower().split("@")[-1].split(":")[0]
        return host[4:] if host.startswith("www.") else host
    except Exception:
        return ""


class SearchWorker(QThread):
    results_ready = Signal(list, list)

    def __init__(self, query, engines=None, seeds=None):
        super().__init__()
        self.query = query
        self.seeds = seeds or []
        self.store_search_reports = []

    def run(self):
        try:
            data = discover(self.query, deep=True, extra_seeds=self.seeds)
            results = data.get("results", [])
            self.store_search_reports = data.get("store_search_reports", [])
            for item in results:
                item["engine"] = "Robot własny"
            errors = data.get("errors", [])
            if not results:
                errors = [f"Sprawdzono {data.get('pages_checked', 0)} stron; sklepy nie zwróciły odczytanych wyników."] + errors
            self.results_ready.emit(results, errors)
        except Exception as exc:
            self.results_ready.emit([], [f"Błąd własnego indeksowania: {exc}"])


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
                         "Połączenie HTTPS dostępne"))
        except Exception as exc:
            elapsed = time.monotonic() - started
            rows.append(("Internet (HTTPS)", "Brak potwierdzenia", 0, f"{elapsed:.1f} s",
                         str(exc)[:220]))
        started = time.monotonic()
        try:
            data = discover("Raspberry Pi 5", deep=False)
            elapsed = time.monotonic() - started
            rows.append(("Własny robot", "Działa" if data.get("pages_checked", 0) else "Brak stron",
                         data.get("pages_checked", 0), f"{elapsed:.1f} s",
                         f"Domeny startowe: {data.get('seed_count', 0)}; "
                         f"oferty: {len(data.get('results', []))}; "
                         f"odrzucone: {data.get('pages_rejected', 0)}"))
            rows.append(("Baza SQLite", "Działa", len(data.get("sources", [])), "-",
                         "Odczyt i zapis indeksu lokalnego"))
        except Exception as exc:
            elapsed = time.monotonic() - started
            rows.append(("Własny robot", "Błąd", 0, f"{elapsed:.1f} s", str(exc)[:220]))
        self.diagnostics_ready.emit(rows)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"Przeglądarka {APP_VERSION} — własny robot indeksujący")
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
        subtitle = QLabel("Własny robot indeksujący publiczne strony — bez zewnętrznych wyszukiwarek.")
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
        self.provider.addItems(["Indeks własny"])
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
        self.provider.hide()
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
        self.table.setHorizontalHeaderLabels(["Oferta / strona", "Opis", "Metoda", "Domena"])
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
        self.discovery_table = QTableWidget(0, 6)
        self.discovery_table.setHorizontalHeaderLabels(["Produkt / strona", "Cena", "Dostępność wg sklepu", "Domena", "Opis", "Adres"])
        for col in (0, 1, 2, 3, 5): self.discovery_table.horizontalHeader().setSectionResizeMode(col, QHeaderView.ResizeToContents)
        self.discovery_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.Stretch)
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
            "Test sprawdza rzeczywistą dostępność publicznych stron i działanie lokalnego indeksowania. "
            "Wynik zależy od internetu, regionu, limitów i zabezpieczeń dostawcy."
        )
        intro.setWordWrap(True)
        root.addWidget(intro)
        self.diag_button = QPushButton("Sprawdź robot i bazę lokalną")
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
            "Robot respektuje robots.txt i nie omija CAPTCHA ani logowania."
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
        <p><b>Wersja:</b> 0.5.0</p>
        <p>Przeglądarka jest niezależnym narzędziem do odkrywania publicznych stron i ofert. Nie jest sklepem ani platformą sprzedażową.</p>
        <h3>Własny mechanizm wyszukiwania</h3>
        <ul>
          <li>Program nie korzysta z Google, Bing, Brave, DuckDuckGo ani innych zewnętrznych wyszukiwarek.</li>
          <li>Robot odwiedza domeny startowe z lokalnego katalogu, publiczne mapy witryn, linki wewnętrzne i domeny wcześniej zweryfikowane.</li>
          <li>Treść stron, rozpoznane produkty, ceny i źródła zapisuje w lokalnej bazie SQLite.</li>
          <li>Można dodawać własne adresy sklepów i producentów. Katalog startowy jest punktem początkowym, a nie kompletnym indeksem Internetu.</li>
        </ul>
        <h3>Połączenia i prywatność</h3>
        <ul>
          <li>Program wykonuje połączenia HTTPS bezpośrednio do publicznych witryn, które indeksuje.</li>
          <li>Zapytanie jest przetwarzane lokalnie; witryny otrzymują zwykłe żądania pobrania publicznych stron.</li>
          <li>Nie uruchamia serwera na komputerze i nie wymaga otwierania portów na routerze.</li>
          <li>Otwieranie wybranej oferty odbywa się w domyślnej przeglądarce Windows.</li>
        </ul>
        <h3>Weryfikacja wyników</h3>
        <ul>
          <li>Wyniki z wewnętrznej wyszukiwarki sklepu są odczytywane z listy zwróconej przez sklep; robot nie ocenia ponownie dostępności ani nie odrzuca wyniku z powodu braku ceny.</li>
          <li>Cena i dostępność są przepisywane wyłącznie wtedy, gdy sklep pokazuje je w wyniku. Brak danych oznacza brak informacji.</li>
          <li>Robot respektuje robots.txt, ogranicza tempo pobierania i nie omija logowania, CAPTCHA, paywalli ani zabezpieczeń antybotowych.</li>
          <li>Nie gwarantuje kompletności Internetu. Wyniki zależą od katalogu startowego, publicznej dostępności stron i jakości ich map witryn.</li>
        </ul>
        <p><b>Podpis cyfrowy:</b> podpis Authenticode wymaga certyfikatu wydawcy. Brak podpisu jest jawnie oznaczany w artefaktach kompilacji.</p>
        """)
        root.addWidget(info, 1)
        return tab

    def start_search(self):
        query = self.query.text().strip()
        if not query:
            QMessageBox.information(self, "Brak zapytania", "Wpisz nazwę produktu, części lub temat.")
            self.query.setFocus()
            return
        seeds = []
        self.search_button.setEnabled(False)
        self.progress.show()
        self.status.setText("Program wysyła zapytania do publicznych wyszukiwarek sklepów i odczytuje ich wyniki…")
        self.count.setText("Wyszukiwanie…")
        self.table.setRowCount(0)
        self.worker = SearchWorker(query, seeds=seeds)
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
            snippet = result.get("snippet", "")
            if result.get("store_total_count") is not None:
                snippet = (f"Sklep zgłosił łącznie {result['store_total_count']} wyników; "
                           f"robot odczytał {result.get('store_returned_cards', 0)} kart. " + snippet)
            self.table.setItem(row, 1, QTableWidgetItem(snippet))
            self.table.setItem(row, 2, QTableWidgetItem(result.get("source", "Robot własny")))
            self.table.setItem(row, 3, QTableWidgetItem(domain_of(result["url"])))
        self.search_button.setEnabled(True)
        self.progress.hide()
        self.count.setText(f"{len(results)} kart produktów")
        reports = getattr(self.worker, "store_search_reports", [])
        if reports:
            summaries = []
            for report in reports:
                if report.get("store_reported_total") is not None:
                    store_count = f"sklep zgłosił {report['store_reported_total']}"
                else:
                    store_count = f"odczytano {report.get('returned_cards', 0)} kart"
                summaries.append(f"{report.get('domain')}: {store_count}")
            self.status.setText("Wyniki sklepów (robot tylko odczytuje): " + "; ".join(summaries[:8]))
        elif results:
            self.status.setText("Odczytano karty produktów zwrócone przez wyszukiwarki sklepów; sklepy nie podały łącznej liczby wyników.")
        if errors and not results and not reports:
            self.status.setText("Brak wyników odczytanych z wyszukiwarek sprawdzonych sklepów. To nie dowodzi, że produktu nie ma w tych sklepach.")
            QMessageBox.warning(
                self, "Problem z wyszukiwaniem",
                "Nie udało się pobrać wyników:\n\n" + "\n".join(errors[:6]) +
                "\n\nDodaj adresy sklepów w zakładce Odkrywanie ofert, aby rozszerzyć indeks."
            )
        elif errors and not reports:
            self.status.setText(
                f"Pokazuję dostępne wyniki. Niedostępne źródła: {len(errors)}. "
                "Szczegóły w diagnostyce."
            )
        elif results and not reports:
            self.status.setText(
                "Wyniki pochodzą z wyszukiwarek poszczególnych sklepów; robot tylko odczytuje ich odpowiedzi."
            )
        elif not reports:
            self.status.setText("Brak wyników dla tego zapytania.")

    def run_discovery(self):
        query = self.discovery_query.text().strip()
        if not query:
            QMessageBox.information(self, "Brak zapytania", "Wpisz nazwę produktu lub części.")
            self.discovery_query.setFocus()
            return
        if self.discovery_worker and self.discovery_worker.isRunning(): return
        self.discovery_button.setEnabled(False)
        self.discovery_status.setText("Sprawdzam strony sklepów i produktów; odrzucam niepowiązane witryny…")
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
            self.discovery_table.setItem(row, 2, QTableWidgetItem(item.get("availability") or "Brak informacji od sklepu"))
            self.discovery_table.setItem(row, 3, QTableWidgetItem(item.get("domain", "")))
            self.discovery_table.setItem(row, 4, QTableWidgetItem(item.get("snippet", "")))
            self.discovery_table.setItem(row, 5, QTableWidgetItem(item.get("url", "")))
        self.discovery_button.setEnabled(True)
        self.discovery_status.setText(f"Sprawdzone strony: {data.get('pages_checked', 0)} | odrzucone: {data.get('pages_rejected', 0)} | zweryfikowane domeny: {len(data.get('sources', []))} | adresy startowe: {data.get('seed_count', 0)} | dopasowane oferty: {len(rows)}")
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
            f"{internet_text}. Własny robot korzysta z lokalnego katalogu domen i bazy SQLite. "
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
