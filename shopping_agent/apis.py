# apis.py
import json
import os
import random
import re
import time
import uuid
import requests
from pathlib import Path
from datetime import datetime
from typing import Any, Dict, List, Optional

from playwright.sync_api import sync_playwright, Page, TimeoutError as PWTimeout
from states import NormalizedListing, AgentState
from pydantic import BaseModel, Field

# ──────────────────────────────────────────────
# Config
# ──────────────────────────────────────────────

RUNS_DIR = Path("./agent_runs")
LOCAL_IMAGES_DIR = Path("./local_agent_storage")

OLLAMA_API_URL = "http://localhost:11434/api/generate"
OLLAMA_MODEL = "qwen2.5:32b-instruct"

VIEWPORT_WIDTH = 1280
VIEWPORT_HEIGHT = 1600

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
]

CURRENCY_SYMBOLS = {"$": "USD", "£": "GBP", "€": "EUR", "₹": "INR", "¥": "JPY"}


class BlockedError(Exception):
    """Raised when Amazon serves a CAPTCHA / bot-check page instead of the product."""


class FetchError(Exception):
    pass


# ──────────────────────────────────────────────
# State helpers
# ──────────────────────────────────────────────


def _empty_listing(url: str, source_type: str = "seed_url") -> NormalizedListing:
    return NormalizedListing(
        title="",
        description="",
        raw_bullets=[],
        price=None,
        price_raw="",
        currency="",
        free_shipping=False,
        condition="unknown",
        availability="",
        rating=None,
        review_count=None,
        brand="",
        asin="",
        marketplace_url=url,
        image_urls=[],
        local_image_paths=[],
        source_type=source_type,
    )


def new_state(url: str) -> AgentState:
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:6]
    return AgentState(
        run_id=run_id,
        initial_url=url,
        target_listing=_empty_listing(url, source_type="seed_url"),
        normalized_listings=[],
        validated_listings=[],
        final_comparison_report="",
        error_count=0,
        current_node="",
        next_node=None,
    )


def save_state(state: AgentState) -> Path:
    run_dir = RUNS_DIR / state["run_id"]
    run_dir.mkdir(parents=True, exist_ok=True)
    path = run_dir / "state.json"
    path.write_text(json.dumps(state, indent=2, ensure_ascii=False))
    return path


def load_state(run_id: str) -> AgentState:
    path = RUNS_DIR / run_id / "state.json"
    return json.loads(path.read_text())


def enter_node(
    state: AgentState, node: str, next_node: Optional[str] = None
) -> AgentState:
    state["current_node"] = node
    state["next_node"] = next_node
    print(f"[node] -> {node}")
    return state


# ──────────────────────────────────────────────
# Image download (shared)
# ──────────────────────────────────────────────


def download_images_for_listing(listing: NormalizedListing) -> NormalizedListing:
    LOCAL_IMAGES_DIR.mkdir(parents=True, exist_ok=True)

    def download_one(url: str) -> Optional[str]:
        if not url or not url.startswith("http"):
            return None
        path_lower = url.split("?")[0].lower()
        if not any(
            path_lower.endswith(ext) for ext in {".jpg", ".jpeg", ".png", ".webp"}
        ):
            return None
        try:
            r = requests.get(url, timeout=15.0)
            r.raise_for_status()
            ext = path_lower.split(".")[-1]
            local_path = LOCAL_IMAGES_DIR / f"{uuid.uuid4()}.{ext}"
            local_path.write_bytes(r.content)
            return str(local_path)
        except Exception as e:
            print(f"[download] Failed {url}: {e}")
            return None

    results = [download_one(u) for u in listing["image_urls"]]
    listing["local_image_paths"] = [p for p in results if p]
    return listing


# ──────────────────────────────────────────────
# Extraction JS (unchanged)
# ──────────────────────────────────────────────

_EXTRACT_JS = r"""
() => {
    const text = (el) => (el ? el.innerText.trim() : "");
    const attr = (el, a) => (el ? el.getAttribute(a) : null);
    const data = {};
    data.title = text(document.querySelector('#productTitle'));
    data.brand = text(document.querySelector('#bylineInfo'))
        .replace(/^Visit the /i, '').replace(/ Store$/i, '')
        .replace(/^Brand: /i, '');
    const priceSelectors = [
        '#corePrice_feature_div .a-offscreen',
        '#corePriceDisplay_desktop_feature_div .a-offscreen',
        '#price_inside_buybox', '#priceblock_ourprice',
        '#priceblock_dealprice', '.a-price .a-offscreen',
    ];
    let priceRaw = '';
    for (const sel of priceSelectors) {
        const el = document.querySelector(sel);
        if (el && el.innerText.trim()) { priceRaw = el.innerText.trim(); break; }
    }
    data.price_raw = priceRaw;
    data.availability = text(document.querySelector('#availability span'))
        || text(document.querySelector('#availability'));
    const deliveryText = text(document.querySelector('#deliveryBlockMessage'))
        || text(document.querySelector('#mir-layout-DELIVERY_BLOCK'));
    data.free_shipping = /free/i.test(deliveryText);
    const buybox = document.querySelector('#buybox, #desktop_buybox, #tabular-buybox');
    const buyboxText = text(buybox);
    if (/renewed|refurbished/i.test(buyboxText)) data.condition = 'refurbished';
    else if (/\bused\b/i.test(buyboxText)) data.condition = 'used';
    else if (buyboxText) data.condition = 'new';
    else data.condition = 'unknown';
    const ratingText = attr(document.querySelector('#acrPopover'), 'title')
        || text(document.querySelector('span.a-icon-alt'));
    const ratingMatch = ratingText && ratingText.match(/([\d.]+)\s*out of/);
    data.rating = ratingMatch ? parseFloat(ratingMatch[1]) : null;
    const reviewCountText = text(document.querySelector('#acrCustomerReviewText'));
    const reviewMatch = reviewCountText && reviewCountText.replace(/,/g, '').match(/(\d+)/);
    data.review_count = reviewMatch ? parseInt(reviewMatch[1], 10) : null;
    data.raw_bullets = Array.from(document.querySelectorAll('#feature-bullets li span.a-list-item'))
        .map(el => el.innerText.trim()).filter(Boolean);
    const asinInput = document.querySelector('input#ASIN, input[name="ASIN"]');
    data.asin = asinInput ? asinInput.value : '';
    const urls = new Set();
    const og = document.querySelector('meta[property="og:image"]');
    if (og && og.content) urls.add(og.content);
    document.querySelectorAll('#imgTagWrapperId img, #altImages img, #landingImage, #imgBlkFront').forEach(el => {
        const dyn = el.getAttribute('data-a-dynamic-image');
        if (dyn) { try { Object.keys(JSON.parse(dyn)).forEach(u => urls.add(u)); } catch (e) {} }
        const hires = el.getAttribute('data-old-hires');
        if (hires) urls.add(hires);
        if (el.src && !el.src.startsWith('data:')) urls.add(el.src);
    });
    data.image_urls = Array.from(urls).slice(0, 30);
    try {
        const ld = document.querySelector('script[type="application/ld+json"]');
        if (ld) data.json_ld = JSON.parse(ld.textContent);
    } catch (e) {}
    data.page_title = document.title || '';
    data.is_captcha = /Robot Check|Enter the characters you see below/i.test(document.body.innerText)
        || /validateCaptcha/i.test(window.location.href);
    return data;
}
"""


def parse_price(price_raw: str) -> Optional[float]:
    if not price_raw:
        return None
    m = re.search(r"[\d,]+\.?\d*", price_raw)
    if not m:
        return None
    try:
        return float(m.group(0).replace(",", ""))
    except ValueError:
        return None


def detect_currency(price_raw: str) -> str:
    for sym, code in CURRENCY_SYMBOLS.items():
        if sym in price_raw:
            return code
    m = re.search(r"\b([A-Z]{3})\b", price_raw)
    return m.group(1) if m else ""


def scrape_amazon_page(url: str, debug_screenshot: bool = False) -> Dict[str, Any]:
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled"],
        )
        context = browser.new_context(
            viewport={"width": VIEWPORT_WIDTH, "height": VIEWPORT_HEIGHT},
            user_agent=random.choice(USER_AGENTS),
            locale="en-US",
        )
        page: Page = context.new_page()

        last_err = None
        for attempt in range(3):
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=45_000)
                break
            except PWTimeout as e:
                last_err = e
                time.sleep(1.5 * (attempt + 1))
        else:
            browser.close()
            raise last_err

        try:
            btn = page.locator("text='Continue shopping'")
            if btn.is_visible(timeout=2000):
                btn.click()
                page.wait_for_load_state("domcontentloaded")
        except Exception:
            pass

        try:
            page.wait_for_selector("#productTitle, #ppd", timeout=15_000)
        except PWTimeout:
            pass

        page.wait_for_timeout(random.randint(800, 1500))

        data = page.evaluate(_EXTRACT_JS)

        if debug_screenshot:
            try:
                el = page.locator("#ppd")
                raw = el.screenshot(type="png")
                fname = f"debug_{datetime.now():%Y%m%d_%H%M%S_%f}.png"
                Path(fname).write_bytes(raw)
                print(f"[scrape] Saved debug screenshot -> {fname}")
            except Exception as e:
                print(f"[scrape] Debug screenshot skipped: {e}")

        browser.close()

    if data.get("is_captcha"):
        raise BlockedError(
            f"Amazon served a bot-check page for {url} (title: {data.get('page_title')!r}). "
            "Retry later, slow down request rate, or use a residential proxy / different IP."
        )

    return data


def extract_and_normalize_amazon(url: str) -> Dict[str, Any]:
    """
    Pure scraping component.
    Accepts a URL, fetches page structure, normalizes data, and returns a raw dictionary.
    Raises explicit errors so the parent state can track fallback routing.
    """
    # 1. Define standard structure template
    listing = {
        "title": "",
        "description": "",
        "raw_bullets": [],
        "price": None,
        "price_raw": "",
        "currency": "",
        "free_shipping": False,
        "condition": "unknown",
        "availability": "",
        "rating": None,
        "review_count": None,
        "brand": "",
        "asin": "",
        "marketplace_url": url,
        "image_urls": [],
        "local_image_paths": [],
        "source_type": "seed_url",
    }

    # 2. Perform raw scrape execution
    data = scrape_amazon_page(url)
    if not data or data.get("is_captcha"):
        raise PermissionError("Blocked by CAPTCHA or missing response data.")

    # 3. Population and normalization mappings
    listing["title"] = data.get("title", "")
    listing["brand"] = data.get("brand", "")
    listing["raw_bullets"] = data.get("raw_bullets", [])
    listing["price_raw"] = data.get("price_raw", "")
    listing["price"] = parse_price(data.get("price_raw", ""))
    listing["currency"] = detect_currency(data.get("price_raw", ""))
    listing["free_shipping"] = bool(data.get("free_shipping"))
    listing["condition"] = data.get("condition", "unknown")
    listing["availability"] = data.get("availability", "")
    listing["rating"] = data.get("rating")
    listing["review_count"] = data.get("review_count")
    listing["asin"] = data.get("asin", "")
    listing["image_urls"] = clean_image_urls(data.get("image_urls", []))

    # JSON-LD Fallback processing
    ld = data.get("json_ld") or {}
    if isinstance(ld, dict):
        if not listing["price"] and "offers" in ld:
            offers = ld["offers"] if isinstance(ld["offers"], dict) else {}
            try:
                listing["price"] = float(offers.get("price"))
                listing["currency"] = offers.get("priceCurrency", listing["currency"])
            except (TypeError, ValueError):
                pass
        if listing["rating"] is None and "aggregateRating" in ld:
            try:
                listing["rating"] = float(ld["aggregateRating"].get("ratingValue"))
                listing["review_count"] = (
                    int(ld["aggregateRating"].get("reviewCount", 0))
                    or listing["review_count"]
                )
            except (TypeError, ValueError, AttributeError):
                pass

    if not listing["title"]:
        raise ValueError("Extraction succeeded but title parameter was blank.")

    return listing


def clean_image_urls(raw_urls: List[str]) -> List[str]:
    clean: List[str] = []
    for u in raw_urls:
        if "images-amazon.com" in u or "media-amazon.com" in u:
            u = re.sub(r"\._[a-zA-Z0-9_,-]+_\.", ".", u)
        if u not in clean:
            clean.append(u)
    return clean


# ──────────────────────────────────────────────
# LLM summary
# ──────────────────────────────────────────────


class Queries(BaseModel):
    queries: List[str] = Field(
        description="A list of highly targeted search queries for similar products."
    )


def generate_queries(state: AgentState) -> List[str]:

    brand = state["target_listing"]["brand"]
    title = state["target_listing"]["title"]
    bullets = state["target_listing"]["raw_bullets"]

    prompt = (
        f"Product title: {title}\n\n"
        f"Product brand: {brand}\n\n"
        f"Feature bullets (verbatim from the listing):\n"
        + "\n".join(f"- {b}" for b in bullets)
        + "\n\nGenerate 3 to 5 distinct, highly-targeted search queries to find similar products. Be specific that you are shopping for them."
    )
    try:
        resp = requests.post(
            OLLAMA_API_URL,
            json={
                "model": OLLAMA_MODEL,
                "prompt": prompt,
                "stream": False,
                "format": Queries.model_json_schema(),
            },
            timeout=120.0,
        )
        resp.raise_for_status()
        response = resp.json().get("response", "").strip()

        validated = Queries.model_validate_json(response)
        return validated.queries

    except Exception as e:
        print(f"[summarize] Ollama call failed or validation failed: {e}")
        return None


# ──────────────────────────────────────────────
# Fallback raw HTML fetch
# ──────────────────────────────────────────────

_FALLBACK_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


def fetch_raw_html(url: str, timeout: float = 10.0, max_retries: int = 2) -> str:
    """
    Plain GET, no JS rendering. Used only as a crude fallback when the
    primary Playwright-based scraper has already failed.
    """
    last_exc: Optional[Exception] = None

    for attempt in range(max_retries + 1):
        try:
            resp = requests.get(
                url, headers=_FALLBACK_HEADERS, timeout=timeout, allow_redirects=True
            )
        except (requests.ConnectionError, requests.Timeout) as e:
            last_exc = e
            print(f"[fetch_raw_html] attempt {attempt + 1} network error: {e}")
            continue

        if resp.status_code == 200:
            return resp.text

        if resp.status_code in (429, 503):
            last_exc = FetchError(f"status {resp.status_code} (rate-limited/blocked)")
            print(
                f"[fetch_raw_html] attempt {attempt + 1} got {resp.status_code}, retrying"
            )
            continue

        raise FetchError(f"Non-200 status {resp.status_code} for {url}")

    raise FetchError(f"Failed after {max_retries + 1} attempts: {last_exc}")
