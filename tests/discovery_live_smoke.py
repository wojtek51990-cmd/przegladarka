"""Live crawler test. No external search engines are queried."""
import tempfile
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.discovery import discover

QUERY = "Raspberry Pi 5"

def main():
    with tempfile.TemporaryDirectory() as tmp:
        data = discover(QUERY, deep=False, db_path=Path(tmp) / "live.sqlite3")
    print("External search engines used: no")
    print(f"Bootstrap domains: {data.get('seed_count', 0)}")
    print(f"Pages checked: {data.get('pages_checked', 0)}")
    print(f"Pages rejected: {data.get('pages_rejected', 0)}")
    print(f"Verified commercial sources: {len(data.get('sources', []))}")
    print(f"Confirmed matching offers: {len(data.get('results', []))}")
    for item in data.get("results", [])[:10]:
        print(f"- {item.get('title')} | {item.get('price','')} {item.get('currency','')} | {item.get('url')}")
    for error in data.get("errors", [])[:12]:
        print(f"NOTE: {error}")
    if data.get("pages_checked", 0) == 0:
        raise SystemExit("FAIL: crawler inspected no public HTML pages.")
    if not data.get("results"):
        raise SystemExit("INCONCLUSIVE: crawler ran, but confirmed no live matching offer.")

if __name__ == "__main__":
    main()
