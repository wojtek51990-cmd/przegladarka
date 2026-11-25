"""Live network check. Bing is the release gate; other providers are informative."""
import concurrent.futures
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.main import ENGINE_NAMES, SEARCHERS


QUERY = "sprężarka Embraco EMT"


def check(name):
    try:
        rows = SEARCHERS[name](QUERY)
        return name, True, len(rows), "OK"
    except Exception as exc:
        return name, False, 0, ascii(str(exc))


def main():
    results = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(ENGINE_NAMES)) as pool:
        futures = [pool.submit(check, name) for name in ENGINE_NAMES]
        for future in concurrent.futures.as_completed(futures):
            name, ok, count, detail = future.result()
            results[name] = (ok, count, detail)

    for name in ENGINE_NAMES:
        ok, count, detail = results[name]
        state = f"OK - {count} results" if ok else f"unavailable - {detail}"
        print(f"{name}: {state}")

    if not results.get("Bing", (False, 0, ""))[0]:
        raise SystemExit("Bing did not return results; release smoke test failed.")
    print("Required provider Bing: OK. Other providers are reported, not release blockers.")


if __name__ == "__main__":
    main()
