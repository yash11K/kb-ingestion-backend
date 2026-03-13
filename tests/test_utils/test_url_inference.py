"""Unit tests for src/utils/url_inference.py."""

from __future__ import annotations

from src.utils.url_inference import (
    infer_brand,
    infer_namespace,
    infer_region,
    normalize_for_matching,
    normalize_url,
)

# Default locale map matching Settings defaults
LOCALE_MAP = {
    "en": "nam",
    "en-us": "nam",
    "en-ca": "nam",
    "en-gb": "emea",
    "en-ie": "emea",
    "de": "emea",
    "fr": "emea",
    "en-au": "apac",
    "en-nz": "apac",
}

# Default namespace list matching Settings defaults
NAMESPACE_LIST = [
    "locations",
    "products-and-services",
    "protections-and-coverages",
    "rental-addons",
    "long-term-car-rental",
    "one-way-car-rentals",
    "miles-points-and-partners",
    "meetings-and-groups",
    "car-sales",
    "faq",
    "customer-service",
    "travel-guides",
]


# ---------------------------------------------------------------------------
# normalize_url
# ---------------------------------------------------------------------------

class TestNormalizeUrl:
    def test_strips_trailing_slash(self):
        assert normalize_url("https://www.avis.com/en/products/") == "https://www.avis.com/en/products"

    def test_strips_query_params(self):
        assert normalize_url("https://www.avis.com/en/products?q=1&page=2") == "https://www.avis.com/en/products"

    def test_strips_fragment(self):
        assert normalize_url("https://www.avis.com/en/products#section") == "https://www.avis.com/en/products"

    def test_strips_all_extras(self):
        assert normalize_url("https://www.avis.com/en/products/?q=1#top") == "https://www.avis.com/en/products"

    def test_already_clean(self):
        assert normalize_url("https://www.avis.com/en/products") == "https://www.avis.com/en/products"

    def test_root_path(self):
        result = normalize_url("https://www.avis.com/")
        assert result == "https://www.avis.com"

    def test_idempotent(self):
        url = "https://www.avis.com/en/products/?q=1#top"
        once = normalize_url(url)
        twice = normalize_url(once)
        assert once == twice


# ---------------------------------------------------------------------------
# infer_brand
# ---------------------------------------------------------------------------

class TestInferBrand:
    def test_avis(self):
        assert infer_brand("https://www.avis.com/en/products.model.json") == "avis"

    def test_budget(self):
        assert infer_brand("https://www.budget.com/en/faq") == "budget"

    def test_co_uk_domain(self):
        assert infer_brand("https://www.budget.co.uk/en-gb/faq") == "budget"

    def test_no_www(self):
        assert infer_brand("https://avis.com/en/products") == "avis"

    def test_unknown_for_empty(self):
        assert infer_brand("") == "unknown"


# ---------------------------------------------------------------------------
# infer_region
# ---------------------------------------------------------------------------

class TestInferRegion:
    def test_en_maps_to_nam(self):
        assert infer_region("https://www.avis.com/en/products", LOCALE_MAP) == "nam"

    def test_en_us_maps_to_nam(self):
        assert infer_region("https://www.avis.com/en-us/products", LOCALE_MAP) == "nam"

    def test_en_gb_maps_to_emea(self):
        assert infer_region("https://www.avis.com/en-gb/faq", LOCALE_MAP) == "emea"

    def test_en_au_maps_to_apac(self):
        assert infer_region("https://www.avis.com/en-au/products", LOCALE_MAP) == "apac"

    def test_de_maps_to_emea(self):
        assert infer_region("https://www.avis.com/de/products", LOCALE_MAP) == "emea"

    def test_unknown_locale(self):
        assert infer_region("https://www.avis.com/ja/products", LOCALE_MAP) == "unknown"

    def test_no_path(self):
        assert infer_region("https://www.avis.com", LOCALE_MAP) == "unknown"

    def test_model_json_url(self):
        assert infer_region("https://www.avis.com/en-gb/faq.model.json", LOCALE_MAP) == "emea"


# ---------------------------------------------------------------------------
# infer_namespace
# ---------------------------------------------------------------------------

class TestInferNamespace:
    def test_products_and_services(self):
        assert infer_namespace(
            "https://www.avis.com/en/products-and-services/products", NAMESPACE_LIST
        ) == "products-and-services"

    def test_faq(self):
        assert infer_namespace("https://www.avis.com/en/faq", NAMESPACE_LIST) == "faq"

    def test_customer_service(self):
        assert infer_namespace(
            "https://www.avis.com/en/customer-service/contact", NAMESPACE_LIST
        ) == "customer-service"

    def test_no_match_returns_general(self):
        assert infer_namespace("https://www.avis.com/en/about-us", NAMESPACE_LIST) == "general"

    def test_model_json_suffix(self):
        assert infer_namespace(
            "https://www.avis.com/en/faq.model.json", NAMESPACE_LIST
        ) == "faq"

    def test_deep_path(self):
        assert infer_namespace(
            "https://www.avis.com/en/locations/us/new-york", NAMESPACE_LIST
        ) == "locations"


# ---------------------------------------------------------------------------
# normalize_for_matching
# ---------------------------------------------------------------------------

class TestNormalizeForMatching:
    def test_relative_path(self):
        assert normalize_for_matching("/en/products-and-services/products") == "/en/products-and-services/products"

    def test_full_model_json_url(self):
        result = normalize_for_matching(
            "https://www.avis.com/en/products-and-services/products.model.json"
        )
        assert result == "/en/products-and-services/products"

    def test_relative_and_full_match(self):
        relative = normalize_for_matching("/en/products-and-services/products")
        full = normalize_for_matching(
            "https://www.avis.com/en/products-and-services/products.model.json"
        )
        assert relative == full

    def test_strips_trailing_slash(self):
        assert normalize_for_matching("/en/products/") == "/en/products"

    def test_lowercased(self):
        assert normalize_for_matching("/EN/Products") == "/en/products"

    def test_empty_path(self):
        assert normalize_for_matching("") == ""
