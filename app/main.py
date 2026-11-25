import sys
import re
import concurrent.futures
from datetime import datetime
from urllib.parse import quote_plus, urlparse, parse_qs, unquote
import webbrowser

import requests
from bs4 import BeautifulSoup
from PySide6.QtCore import QThread, Signal, Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QComboBox, QTableWidget,
    QTableWidgetItem, QHeaderView, QMessageBox, QProgressBar,
    QAbstractItemView, QMenu
)

APP_NAME = "Przeglądarka"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
}
TIMEOUT = 14


def clean_url(url):
    if not url:
        return ""
    url = unquote(url.strip())
    if url.startswith("//"):
        url = "https:" + url
    if "duckduckgo.com/l/?" in url:
        try:
            url = parse_qs(urlparse(url).query).get("uddg", [url])[0]
        except Exception:
            pass
    if not url.startswith(("http://", "https://")):
        return ""
    return url.split("#", 1)[0]


def domain_of(url):
    try:
        host = urlparse(url).netloc.lower()
        return host[4:] if host.startswith("www.") else host
    except Exception:
        return ""


def search_duckduckgo(query):
    url = "https://html.duckduckgo.com/html/?q=" + quote_plus(query)
    response = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    found = []
    for item in soup.select(".result"):
        link = item.select_one("a.result__a")
        if not link:
            continue
        target = clean_url(link.get("href", ""))
        if not target or "duckduckgo.com" in domain_of(target):
            continue
        snippet = item.select_one(".result__snippet")
        found.append({
            "title": link.get_text(" ", strip=True),
            "url": target,
            "snippet": snippet.get_text(" ", strip=True) if snippet else "",
            "engine": "DuckDuckGo"
        })
    return found


def search_bing(query):
    url = "https://www.bing.com/search?q=" + quote_plus(query)
    response = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    found = []
    for item in soup.select("li.b_algo"):
        link = item.select_one("h2 a")
        if not link:
            continue
        target = clean_url(link.get("href", ""))
        if not target or "bing.com" in domain_of(target):
            continue
        snippet = item.select_one("p")
        found.append({
            "title": link.get_text(" ", strip=True),
            "url": target,
            "snippet": snippet.get_text(" ", strip=True) if snippet else "",
            "engine": "Bing"
        })
    return found


class SearchWorker(QThread):
    results_ready = Signal(list, list)

    def __init__(self, query, provider):
        super().__init__()
        self.query = query
        self.provider = provider

    def run(self):
        tasks = []
        if self.provider in ("Obie wyszukiwarki", "DuckDuckGo"):
            tasks.append(("DuckDuckGo", search_duckduckgo))
        if self.provider in ("Obie wyszukiwarki", "Bing"):
            tasks.append(("Bing", search_bing))
        results, errors = [], []
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            future_map = {pool.submit(fn, self.query): name for name, fn in tasks}
            for future in concurrent.futures.as_completed(future_map):
                name = future_map[future]
                try:
                    results.extend(future.result())
                except Exception as exc:
                    errors.append(f"{name}: {exc}")
        unique, seen = [], set()
        for item in results:
            key = item["url"].rstrip("/").lower()
            if key not in seen:
                seen.add(key)
                unique.append(item)
        self.results_ready.emit(unique, errors)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Przeglądarka — niezależne wyszukiwanie")
        self.resize(1120, 720)
        self.setMinimumSize(780, 520)
        self.worker = None
        self.all_results = []
        self._build_ui()

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(24, 20, 24, 18)
        root.setSpacing(12)

        title = QLabel("PRZEGLĄDARKA")
        title.setFont(QFont("Segoe UI", 22, QFont.Bold))
        title.setStyleSheet("color:#183b56;")
        root.addWidget(title)

        subtitle = QLabel("Szukaj produktów, części, usług i informacji w różnych źródłach internetu.")
        subtitle.setStyleSheet("color:#536575;font-size:13px;")
        root.addWidget(subtitle)

        search_row = QHBoxLayout()
        self.query = QLineEdit()
        self.query.setPlaceholderText("Wpisz, czego szukasz, np. sprężarka Embraco EMT  н...")
        self.query.setMinimumHeight(42)
        self.query.setClearButtonEnabled(True)
        self.query.returnPressed.connect(self.start_search)
        self.provider = QComboBox()
        self.provider.addItems(["Obie wyszukiwarki", "DuckDuckGo", "Bing"])
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
        self.table.setHorizontalHeaderLabels(["Tytuł / źródło", "Opis", "Wyszukiwarka", "Adres"])
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
        self.table.setStyleSheet(
            "QTableWidget{border:1px solid #d5e0e6;border-radius:6px;gridline-color:#e7edf0;}"
            "QHeaderView::section{background:#edf3f6;padding:8px;border:0;font-weight:bold;}"
            "QTableWidget::item{padding:7px;}"
        )
        root.addWidget(self.table, 1)

        bottom = QHBoxLayout()
        self.open_button = QPushButton("Otwórz zaznaczony wynik")
        self.open_button.clicked.connect(self.open_current)
        self.copy_button = QPushButton("Kopiuj adres")
        self.copy_button.clicked.connect(self.copy_current)
        note = QLabel("Dwuklik otwiera stronę sprzedawcy lub źródła.")
        note.setStyleSheet("color:#71808a;")
        bottom.addWidget(self.open_button)
        bottom.addWidget(self.copy_button)
        bottom.addStretch(1)
        bottom.addWidget(note)
        root.addLayout(bottom)

        self.setStyleSheet("QMainWindow{background:#f7f9fa;} QLineEdit,QComboBox,QPushButton{"
                           "border:1px solid #cbd7de;border-radius:6px;padding:7px;background:white;}"
                           "QPushButton:hover{background:#edf3f6;}")

    def start_search(self):
        query = self.query.text().strip()
        if not query:
            QMessageBox.information(self, "Brak zapytania", "Wpisz nazwę produktu, części lub temat.")
            self.query.setFocus()
            return
        self.search_button.setEnabled(False)
        self.progress.show()
        self.status.setText("Przeszukuję wybrane źródła…")
        self.count.setText("Wyszukiwanie…")
        self.table.setRowCount(0)
        self.worker = SearchWorker(query, self.provider.currentText())
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
            self.status.setText("Nie udało się pobrać wyników. Wyszukiwarka mogła zablokować automatyczne zapytanie.")
            QMessageBox.warning(self, "Problem z wyszukiwaniem",
                                "Nie udało się pobrać wyników:\n\n" + "\n".join(errors[:3]) +
                                "\n\nSpróbuj ponownie później lub wybierz inną wyszukiwarkę.")
        elif errors:
            self.status.setText("Część źródeł nie odpowiedziała. Pokazuję dostępne wyniki.")
        else:
            self.status.setText("Wyniki gotowe. Program nie promuje sprzedawców ani nie obsługuje sprzedaży.")
        if not results and not errors:
            self.status.setText("Brak wyników dla tego zapytania.")

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
