"""
AEM Link Spider — Crawls model.json endpoints starting from a seed URL,
discovers all internal navigable page links, and compiles a full sitemap
of model.json URLs.

Exhaustive by default: keeps crawling until no new links are found.

Usage:
    python link_spider.py https://www.avis.com/en/home.model.json
    python link_spider.py home.model.json --max-pages 200 --delay 0.5
    python link_spider.py home.model.json  (local file as seed)
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

import httpx

# ---------------------------------------------------------------------------
# Link extraction
# ---------------------------------------------------------------------------

# Fields that commonly hold internal page URLs in AEM model.json
_URL_FIELDS = {"url", "ctaLink", "linkUrl", "forgotUsernameOrPasswordLink",
               "createAccountLink", "amazonLandingPageUrl", "loginLandingPageUrl",
               "link", "href", "ctaUrl", "seeAllLinkUrl", "canonicalUrl"}

# Paths that are clearly not content pages (functional / external)
_SKIP_PREFIXES = ("#",)
_SKIP_SUBSTRINGS = ("jcr:", "/dam/", "/conf/", "/libs/", "/settings/")


def _is_internal_page_path(path: str, base_domain: str | None) -> bool:
    """Return True if the path looks like an internal AEM content page."""
    if not path or not isinstance(path, str):
        return False
    if any(path.startswith(p) for p in _SKIP_PREFIXES):
        return False
    if any(s in path for s in _SKIP_SUBSTRINGS):
        return False

    # Absolute URL — only keep if same domain
    if path.startswith("http"):
        parsed = urlparse(path)
        if base_domain and parsed.netloc != base_domain:
            return False
        path = parsed.path

    # Must look like a page path (starts with /) and not just root
    if not path.startswith("/"):
        return False
    if path == "/":
        return False

    # Skip asset/image paths
    if any(path.endswith(ext) for ext in (".jpg", ".jpeg", ".png", ".svg",
                                           ".gif", ".webp", ".pdf", ".css",
                                           ".js", ".ico")):
        return False

    return True


def _normalize_path(raw: str) -> str:
    """Strip to just the path portion, remove trailing slash."""
    if raw.startswith("http"):
        raw = urlparse(raw).path
    return raw.rstrip("/") or "/"


def extract_links(node: object, base_domain: str | None = None) -> set[str]:
    """Recursively walk any JSON structure and pull out internal page paths."""
    found: set[str] = set()

    if isinstance(node, dict):
        for key, value in node.items():
            if key in _URL_FIELDS and isinstance(value, str):
                if _is_internal_page_path(value, base_domain):
                    found.add(_normalize_path(value))
            # Recurse into all values
            found |= extract_links(value, base_domain)

    elif isinstance(node, list):
        for item in node:
            found |= extract_links(item, base_domain)

    return found


# ---------------------------------------------------------------------------
# Fetching
# ---------------------------------------------------------------------------

def _build_model_url(base_url: str, page_path: str) -> str:
    """Turn a page path like /en/customer-service into a full model.json URL."""
    parsed = urlparse(base_url)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    clean = page_path.rstrip("/")
    return f"{origin}{clean}.model.json"


def fetch_model_json(url: str, timeout: int = 30) -> dict | None:
    """Fetch a model.json URL. Returns parsed JSON or None on failure."""
    try:
        resp = httpx.get(url, timeout=timeout, follow_redirects=True)
        if resp.status_code != 200:
            return None
        return resp.json()
    except (httpx.RequestError, httpx.TimeoutException, ValueError):
        return None


def _fmt_bytes(n: int) -> str:
    """Human-readable byte size."""
    if n < 1024:
        return f"{n} B"
    elif n < 1024 * 1024:
        return f"{n / 1024:.1f} KB"
    return f"{n / (1024 * 1024):.1f} MB"


def _fmt_duration(seconds: float) -> str:
    """Human-readable duration."""
    if seconds < 60:
        return f"{seconds:.1f}s"
    mins, secs = divmod(int(seconds), 60)
    return f"{mins}m {secs}s"


# ---------------------------------------------------------------------------
# Spider
# ---------------------------------------------------------------------------

def _save_snapshot(output_path: str, seed_label: str, visited: dict, failed: dict,
                   all_links: set, total_bytes: int, elapsed: float) -> None:
    """Write current crawl state to disk (called after every fetch)."""
    snapshot = {
        "seed": seed_label,
        "status": "in_progress",
        "pages_fetched": len(visited),
        "pages_failed": len(failed),
        "total_links_discovered": len(all_links),
        "total_bytes_downloaded": total_bytes,
        "crawl_duration_seconds": round(elapsed, 2),
        "visited": visited,
        "failed": failed,
        "all_links": sorted(all_links),
    }
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(snapshot, f, indent=2)


def crawl(
    seed: str | dict,
    base_url: str | None = None,
    max_pages: int = 0,
    delay: float = 0,
    timeout: int = 30,
    output_path: str = "spider_results.json",
    seed_label: str = "",
) -> dict:
    """
    BFS crawl starting from a seed URL or local JSON dict.
    Exhaustive by default — keeps going until no new links remain.
    Saves progress to output_path after every successful fetch.

    Args:
        seed: A model.json URL string or a pre-loaded dict.
        base_url: Origin URL used to build model.json URLs from paths.
        max_pages: Stop after fetching this many pages. 0 = unlimited.
        delay: Seconds to wait between requests. 0 = no delay.
        timeout: HTTP timeout per request in seconds.
        output_path: File path to save incremental results.
        seed_label: Label for the seed in output (original CLI arg).

    Returns a dict with:
        - visited: dict mapping page_path -> model.json URL (successfully fetched)
        - failed:  dict mapping page_path -> model.json URL (fetch failed)
        - all_links: set of every internal path discovered
    """
    visited: dict[str, str] = {}   # path -> model_url
    failed: dict[str, str] = {}    # path -> model_url
    all_links: set[str] = set()
    total_bytes: int = 0
    crawl_start = time.time()

    # Determine base domain for same-origin filtering
    base_domain: str | None = None
    if base_url:
        base_domain = urlparse(base_url).netloc

    # Seed can be a local file/dict or a URL
    if isinstance(seed, dict):
        seed_json = seed
        seed_path = _normalize_path(urlparse(base_url).path.replace(".model.json", "")) if base_url else "/en/home"
    else:
        seed_path = _normalize_path(urlparse(seed).path.replace(".model.json", ""))
        if not base_url:
            parsed = urlparse(seed)
            base_url = f"{parsed.scheme}://{parsed.netloc}{seed_path}.model.json"
            base_domain = parsed.netloc
        print(f"🌐 Fetching seed URL: {seed}")
        seed_json = fetch_model_json(seed, timeout)
        if seed_json is None:
            print(f"💀 Could not fetch seed URL: {seed}", file=sys.stderr)
            return {"visited": visited, "failed": failed, "all_links": all_links}

    # Process seed
    visited[seed_path] = base_url or seed
    seed_bytes = len(json.dumps(seed_json))
    total_bytes += seed_bytes
    links = extract_links(seed_json, base_domain)
    all_links |= links

    print(f"🌱 Seed: {seed_path}")
    print(f"   📦 Size: {_fmt_bytes(seed_bytes)}")
    print(f"   🔗 Links discovered: {len(links)}")
    print(f"{'─' * 70}")

    # Save initial state
    _save_snapshot(output_path, seed_label, visited, failed, all_links, total_bytes,
                   time.time() - crawl_start)
    print(f"   💾 Snapshot saved to {output_path}")

    # BFS queue
    queue: list[str] = [p for p in sorted(links) if p != seed_path]

    while queue:
        if max_pages > 0 and len(visited) >= max_pages:
            print(f"\n🛑 Reached --max-pages limit ({max_pages})")
            break

        path = queue.pop(0)

        if path in visited or path in failed:
            continue

        model_url = _build_model_url(base_url, path)
        fetched = len(visited)
        pending = len(queue)
        elapsed = _fmt_duration(time.time() - crawl_start)

        print(f"\n🕷️  [{fetched} done | {pending} queued | ⏱️  {elapsed}]")
        print(f"   📡 {model_url}")

        if delay > 0:
            time.sleep(delay)

        req_start = time.time()
        page_json = fetch_model_json(model_url, timeout)
        req_time = time.time() - req_start

        if page_json is None:
            failed[path] = model_url
            print(f"   ❌ FAILED ({req_time:.2f}s)")
            _save_snapshot(output_path, seed_label, visited, failed, all_links,
                           total_bytes, time.time() - crawl_start)
            continue

        page_bytes = len(json.dumps(page_json))
        total_bytes += page_bytes
        visited[path] = model_url
        print(f"   ✅ OK — {_fmt_bytes(page_bytes)} in {req_time:.2f}s")

        # Discover new links from this page
        new_links = extract_links(page_json, base_domain)
        newly_found = new_links - all_links
        all_links |= new_links

        if newly_found:
            print(f"   🔍 +{len(newly_found)} new link(s) found")
            for link in sorted(newly_found):
                print(f"      🆕 {link}")
                if link not in visited and link not in failed:
                    queue.append(link)
        else:
            print(f"   📭 No new links")

        # Save progress after every successful fetch
        _save_snapshot(output_path, seed_label, visited, failed, all_links,
                       total_bytes, time.time() - crawl_start)
        print(f"   💾 Snapshot saved")

    return {
        "visited": visited,
        "failed": failed,
        "all_links": all_links,
        "total_bytes": total_bytes,
        "elapsed": time.time() - crawl_start,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="AEM model.json link spider")
    parser.add_argument("seed", help="Seed URL (model.json endpoint) or local JSON file path")
    parser.add_argument("--max-pages", type=int, default=0, help="Stop after N pages (default: 0 = unlimited)")
    parser.add_argument("--delay", type=float, default=0, help="Delay between requests in seconds (default: 0)")
    parser.add_argument("--timeout", type=int, default=30, help="HTTP timeout in seconds (default: 30)")
    parser.add_argument("--output", "-o", default="spider_results.json", help="Output file (default: spider_results.json)")
    args = parser.parse_args()

    seed_input = args.seed
    base_url = None

    print()
    print("🕸️  AEM Link Spider")
    print("=" * 70)

    # Support local file as seed
    if Path(seed_input).is_file():
        print(f"📂 Loading local seed file: {seed_input}")
        with open(seed_input, encoding="utf-8") as f:
            seed_data = json.load(f)
        # Need a base URL for building model.json URLs from discovered paths
        canonical = seed_data.get("canonicalUrl", "")
        if canonical:
            parsed = urlparse(canonical)
            base_url = f"{parsed.scheme}://{parsed.netloc}{parsed.path}.model.json"
            print(f"🏠 Base URL (from canonicalUrl): {base_url}")
        else:
            print("💀 Local file has no canonicalUrl — provide a remote seed URL instead.", file=sys.stderr)
            sys.exit(1)
        seed_input = seed_data
    else:
        base_url = seed_input
        print(f"🌐 Remote seed: {seed_input}")

    if args.max_pages > 0:
        print(f"🔒 Max pages: {args.max_pages}")
    else:
        print(f"♾️  Max pages: unlimited (exhaustive crawl)")

    if args.delay > 0:
        print(f"⏳ Delay: {args.delay}s between requests")

    print("=" * 70)

    result = crawl(seed_input, base_url=base_url, max_pages=args.max_pages,
                   delay=args.delay, timeout=args.timeout,
                   output_path=args.output, seed_label=args.seed)

    # Final save with status=complete
    output = {
        "seed": args.seed,
        "status": "complete",
        "pages_fetched": len(result["visited"]),
        "pages_failed": len(result["failed"]),
        "total_links_discovered": len(result["all_links"]),
        "total_bytes_downloaded": result["total_bytes"],
        "crawl_duration_seconds": round(result["elapsed"], 2),
        "visited": result["visited"],
        "failed": result["failed"],
        "all_links": sorted(result["all_links"]),
    }

    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2)

    # Final summary
    elapsed = _fmt_duration(result["elapsed"])
    total = _fmt_bytes(result["total_bytes"])
    v = len(result["visited"])
    f_ = len(result["failed"])
    links = len(result["all_links"])

    print()
    print("=" * 70)
    print("🏁 CRAWL COMPLETE")
    print("=" * 70)
    print(f"   ✅ Pages fetched:    {v}")
    print(f"   ❌ Pages failed:     {f_}")
    print(f"   🔗 Total links:      {links}")
    print(f"   📦 Data downloaded:  {total}")
    print(f"   ⏱️  Duration:         {elapsed}")
    print(f"   💾 Results saved to: {args.output}")
    print("=" * 70)


if __name__ == "__main__":
    main()
