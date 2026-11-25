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
        ("Bing RSS/HTML", search_bing),
        ("DuckDuckGo", search_duckduckgo),
    ):
        try:
            results = search(QUERY)
            print(f"{name}: OK - {len(results)} results for the test query.")
            successful += 1
        except Exception as exc:
            # ascii() prevents legacy Windows console encodings from hiding
            # the real network/parser error behind a UnicodeEncodeError.
            print(f"{name}: unavailable - {ascii(str(exc))}")

    if successful == 0:
        raise SystemExit(
            "Neither search provider returned results. "
            "The hosted runner may be blocked by the providers."
        )


if __name__ == "__main__":
    main()
