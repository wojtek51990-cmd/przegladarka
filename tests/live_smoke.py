"""Optional real-network smoke test for the public search endpoints.

A provider may block automated requests from hosted runners. The workflow runs
this check as non-blocking; deterministic parser tests remain the build gate.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.main import search_bing, search_duckduckgo


QUERY = "sprężarka Embraco EMT"


def main():
    successful = 0
    for name, search in (
        ("DuckDuckGo", search_duckduckgo),
        ("Bing", search_bing),
    ):
        try:
            results = search(QUERY)
            print(f"{name}: OK — {len(results)} wyników dla zapytania testowego.")
            successful += 1
        except Exception as exc:
            print(f"{name}: niedostępna w teście sieciowym — {exc}")

    if successful == 0:
        raise SystemExit(
            "Żadna wyszukiwarka nie zwróciła wyników. "
            "Możliwa blokada ruchu automatycznego z runnera GitHub."
        )


if __name__ == "__main__":
    main()
