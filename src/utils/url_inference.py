"""URL inference utilities for brand, region, and namespace extraction.

These functions derive metadata from AEM URLs so that the API consumer
does not need to supply brand, region, or namespace manually.
"""

from __future__ import annotations

from urllib.parse import urlparse


def normalize_url(url: str) -> str:
    """Strip trailing slashes, query parameters, and fragments for cycle detection.

    Examples:
        >>> normalize_url("https://www.avis.com/en/products/?q=1#top")
        'https://www.avis.com/en/products'
        >>> normalize_url("https://www.avis.com/en/products/")
        'https://www.avis.com/en/products'
    """
    parsed = urlparse(url)
    # Rebuild with only scheme, netloc, and cleaned path
    clean_path = parsed.path.rstrip("/")
    return f"{parsed.scheme}://{parsed.netloc}{clean_path}" if parsed.scheme else clean_path


def infer_brand(url: str) -> str:
    """Extract brand from the URL domain.

    Strips ``www.`` prefix and the TLD suffix to isolate the brand name.

    Examples:
        >>> infer_brand("https://www.avis.com/en/products.model.json")
        'avis'
        >>> infer_brand("https://www.budget.co.uk/en-gb/faq")
        'budget'
    """
    parsed = urlparse(url)
    hostname = parsed.hostname or ""
    # Remove www. prefix
    if hostname.startswith("www."):
        hostname = hostname[4:]
    # Take the first segment before any dot (brand name)
    brand = hostname.split(".")[0] if hostname else "unknown"
    return brand or "unknown"


def infer_region(url: str, locale_map: dict[str, str]) -> str:
    """Extract locale from the URL path and map it to a region code.

    Checks the first one or two path segments for a locale key present in
    *locale_map*.  Tries the two-part locale first (e.g. ``en-gb``) before
    falling back to the single-part locale (e.g. ``en``).

    Examples:
        >>> locale_map = {"en": "nam", "en-gb": "emea", "en-au": "apac"}
        >>> infer_region("https://www.avis.com/en/products", locale_map)
        'nam'
        >>> infer_region("https://www.avis.com/en-gb/faq", locale_map)
        'emea'
    """
    parsed = urlparse(url)
    segments = [s for s in parsed.path.split("/") if s]
    if not segments:
        return "unknown"

    # The locale is typically the first path segment (e.g. /en/ or /en-gb/)
    first_segment = segments[0].lower()

    # Try exact match first (handles en-gb, en-us, etc.)
    if first_segment in locale_map:
        return locale_map[first_segment]

    # Try base language only (e.g. "en" from "en-gb" if "en-gb" wasn't in map)
    base_lang = first_segment.split("-")[0]
    if base_lang in locale_map:
        return locale_map[base_lang]

    return "unknown"


def infer_namespace(url: str, namespace_list: list[str]) -> str:
    """Match URL path segments against the namespace list.

    Returns the first path segment that matches an entry in *namespace_list*,
    or ``"general"`` if no segment matches.

    Examples:
        >>> ns_list = ["products-and-services", "faq", "customer-service"]
        >>> infer_namespace("https://www.avis.com/en/products-and-services/products", ns_list)
        'products-and-services'
        >>> infer_namespace("https://www.avis.com/en/about-us", ns_list)
        'general'
    """
    parsed = urlparse(url)
    segments = [s for s in parsed.path.split("/") if s]

    # Also strip .model.json suffix from the last segment for matching
    cleaned_segments = []
    for seg in segments:
        if seg.endswith(".model.json"):
            seg = seg.removesuffix(".model.json")
        cleaned_segments.append(seg)

    for segment in cleaned_segments:
        if segment in namespace_list:
            return segment

    return "general"


def normalize_for_matching(url: str) -> str:
    """Normalize a URL for confirmed_urls matching.

    Handles both relative paths (e.g. ``/en/products``) and full AEM
    model.json URLs (e.g. ``https://www.avis.com/en/products.model.json``),
    producing a canonical path form so that both formats match each other.

    The canonical form is the path with:
    - ``.model.json`` suffix removed
    - Trailing slashes removed
    - Query parameters and fragments removed
    - Lowercased

    Examples:
        >>> normalize_for_matching("/en/products-and-services/products")
        '/en/products-and-services/products'
        >>> normalize_for_matching("https://www.avis.com/en/products-and-services/products.model.json")
        '/en/products-and-services/products'
    """
    parsed = urlparse(url)
    path = parsed.path.rstrip("/")

    # Remove .model.json suffix
    if path.endswith(".model.json"):
        path = path.removesuffix(".model.json")

    # Remove any remaining trailing slashes after suffix removal
    path = path.rstrip("/")

    # Ensure path starts with /
    if path and not path.startswith("/"):
        path = f"/{path}"

    return path.lower()
