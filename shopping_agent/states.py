from typing import Dict, Any, List, TypedDict, Optional, Annotated
import operator


class NormalizedListing(TypedDict):
    title: str
    description: str  # LLM-cleaned summary (falls back to raw bullets)
    raw_bullets: List[str]  # verbatim feature bullets, no invention
    price: Optional[float]
    price_raw: str
    currency: str
    free_shipping: bool
    condition: str  # "new" | "used" | "refurbished" | "unknown"
    availability: str  # verbatim availability text, e.g. "In Stock"
    rating: Optional[float]
    review_count: Optional[int]
    brand: str
    asin: str
    marketplace_url: str
    image_urls: List[str]
    local_image_paths: List[str]
    source_type: str  # "seed_url" | "image_search" | "text_search"


class AgentState(TypedDict):
    run_id: str
    initial_url: str
    target_listing: NormalizedListing

    queries: List[str]
    urls_to_scrape: List[str]  # Queue of found competitor URLs (e.g., up to 10)
    current_url: str  # The single URL being processed right now

    normalized_listings: Annotated[List[NormalizedListing], operator.add]
    validated_listings: List[NormalizedListing]
    final_comparison_report: str
    error_count: int
    current_node: str
    next_node: Optional[str]
