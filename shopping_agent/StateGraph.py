# graph.py
from langgraph.graph import StateGraph, END
from states import AgentState
from apis import *

import re
import json
from urllib.parse import urlparse
from dotenv import load_dotenv
from ddgs import DDGS

load_dotenv()


def build_graph():
    graph = StateGraph(AgentState)

    # 1. Define Nodes
    graph.add_node("initiate_state", initiate_state)
    graph.add_node("initiate_keyword_search", initiate_keyword_search)
    graph.add_node("process_next_url", process_next_url)
    graph.add_node("handle_scrape_error", handle_scrape_error)
    graph.add_node("terminate_with_error", terminate_with_error)

    graph.set_entry_point("initiate_state")

    # 2. Routing after initial Amazon scrape
    graph.add_conditional_edges(
        "initiate_state",
        lambda state: state["next_node"],
        {
            "initiate_keyword_search": "initiate_keyword_search",
            "handle_scrape_failure": "handle_scrape_error",
        },
    )

    # 3. Handle scrape error fallback routing
    graph.add_conditional_edges(
        "handle_scrape_error",
        lambda state: state["next_node"],
        {
            "initiate_keyword_search": "initiate_keyword_search",  # Re-try seed if it recovered
            "process_next_url": "process_next_url",  # Move on if a competitor failed
            "terminate_with_error": "terminate_with_error",
        },
    )

    # 4. Loop routing for processing the URL queue
    graph.add_conditional_edges(
        "process_next_url",
        lambda state: state["next_node"],
        {
            "process_next_url": "process_next_url",  # Success -> Loop to next item
            "handle_scrape_failure": "handle_scrape_error",  # Failure -> Route to fallback scraping
            "analyze_competitors": END,  # Queue empty -> Complete!
            "terminate_with_error": "terminate_with_error",
        },
    )

    graph.add_conditional_edges(
        "initiate_keyword_search",
        lambda state: state["next_node"],
        {
            "process_next_url": "process_next_url",
            "terminate_with_error": "terminate_with_error",
        },
    )

    graph.add_edge("terminate_with_error", END)
    return graph.compile()


def terminate_with_error(state: AgentState) -> AgentState:
    state["current_node"] = "terminate_with_error"
    state["final_comparison_report"] = (
        f"Could not extract enough product data from {state['initial_url']} "
        f"after {state['error_count']} failures. Please check the URL or try again."
    )
    return state


def handle_scrape_error(state: AgentState) -> AgentState:
    state["current_node"] = "handle_scrape_error"

    # Global panic button: if total errors across the workflow spike too high, abort.
    if state["error_count"] > 10:
        print(
            f"[handle_scrape_error] Total error count ({state['error_count']}) is too high. Terminating."
        )
        state["next_node"] = "terminate_with_error"
        return state

    # 1. Determine which URL we are currently trying to rescue
    url_to_fix = state.get("current_url") or state.get("initial_url")
    print(
        f"[handle_scrape_error] Attempting raw regex fallback extraction for: {url_to_fix}"
    )

    # Initialize a clean dictionary structure for this fallback attempt
    fallback_listing = {
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
        "marketplace_url": url_to_fix,
        "image_urls": [],
        "local_image_paths": [],
        "source_type": "fallback_scrape",
    }

    try:
        html = fetch_raw_html(url_to_fix)
    except Exception as e:
        print(
            f"[handle_scrape_error] Raw HTML fetch fully failed for {url_to_fix}: {e}"
        )
        state["error_count"] += 1
        # Route back to the queue runner if it was a competitor, or fail out if it was the main seed URL
        state["next_node"] = (
            "process_next_url" if state.get("current_url") else "terminate_with_error"
        )
        return state

    # 2. Extract Data via Application/LD+JSON scripts
    title = None
    price = None
    currency = ""

    ld_matches = re.findall(
        r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        html,
        re.DOTALL | re.IGNORECASE,
    )
    for raw in ld_matches:
        try:
            ld = json.loads(raw.strip())
            ld_list = ld if isinstance(ld, list) else [ld]
            for entry in ld_list:
                if not isinstance(entry, dict):
                    continue
                if not title and entry.get("name"):
                    title = entry["name"]
                offers = entry.get("offers")
                if isinstance(offers, list):
                    offers = offers[0] if offers else {}
                if isinstance(offers, dict) and price is None:
                    try:
                        price = float(offers.get("price"))
                        currency = offers.get("priceCurrency", "")
                    except (TypeError, ValueError):
                        pass
        except (json.JSONDecodeError, TypeError):
            continue

    # 3. Fallback to OpenGraph meta tags if JSON-LD missed the title
    if not title:
        og_match = re.search(
            r'<meta[^>]*property=["\']og:title["\'][^>]*content=["\'](.*?)["\']',
            html,
            re.IGNORECASE,
        )
        if og_match:
            title = og_match.group(1).strip()

    # 4. Fallback to standard HTML <title> tag
    if not title:
        title_match = re.search(
            r"<title[^>]*>(.*?)</title>", html, re.DOTALL | re.IGNORECASE
        )
        if title_match:
            title = re.sub(r"\s+", " ", title_match.group(1)).strip()

    # 5. Fallback regex to capture currency symbols and numeric prices
    if price is None:
        price_match = re.search(r"[\$£€]\s?(\d{1,5}(?:[.,]\d{2})?)", html)
        if price_match:
            price_raw = price_match.group(0)
            price = parse_price(price_raw)
            currency = detect_currency(price_raw)

    # 6. Fallback image extraction via OpenGraph image tags
    image_urls = []
    og_image = re.search(
        r'<meta[^>]*property=["\']og:image["\'][^>]*content=["\'](.*?)["\']',
        html,
        re.IGNORECASE,
    )
    if og_image:
        image_urls.append(og_image.group(1))

    # 7. Check if we just hit a brick wall captcha page anyway
    is_captcha = bool(
        re.search(
            r"Robot Check|Enter the characters you see below|captcha",
            html,
            re.IGNORECASE,
        )
    )
    if is_captcha:
        print(
            f"[handle_scrape_error] Warning: Fallback raw scrap hit a CAPTCHA page for {url_to_fix}"
        )
        state["error_count"] += 1

    # Populate our fallback dictionary structure
    fallback_listing["title"] = (
        title or urlparse(url_to_fix).path.strip("/").replace("-", " ")[:100]
    )
    fallback_listing["price"] = price
    fallback_listing["price_raw"] = str(price) if price is not None else ""
    fallback_listing["currency"] = currency
    fallback_listing["image_urls"] = image_urls

    # Inside handle_scrape_error, replace the bottom routing logic:

    # 8. Dynamic Graph Routing based on which URL was processed
    if state.get("current_url"):
        if fallback_listing["title"] and not is_captcha:
            print(
                f"[handle_scrape_error] Successfully salvaged competitor URL: {url_to_fix}"
            )
            return {
                "current_node": "handle_scrape_error",
                "normalized_listings": [
                    NormalizedListing(**fallback_listing)
                ],  # Appends to state
                "next_node": "process_next_url",
            }
        else:
            print(f"[handle_scrape_error] Could not salvage competitor URL. Skipping.")
            return {
                "current_node": "handle_scrape_error",
                "error_count": state.get("error_count", 0) + 1,
                "next_node": "process_next_url",
            }
    else:
        # Handling primary seed URL
        if fallback_listing["title"] and not is_captcha:
            print(f"[handle_scrape_error] Successfully salvaged initial seed URL!")
            return {
                "current_node": "handle_scrape_error",
                "target_listing": fallback_listing,
                "next_node": "initiate_keyword_search",
            }
        else:
            print(f"[handle_scrape_error] Seed completely unsalvageable. Terminating.")
            return {
                "current_node": "handle_scrape_error",
                "next_node": "terminate_with_error",
            }

    return state


def initiate_state(state: AgentState) -> AgentState:
    url = state["initial_url"]
    state["current_node"] = "initiate_state"

    try:
        # Run our separated extraction component
        state["target_listing"] = extract_and_normalize_amazon(url)
        state["next_node"] = "initiate_keyword_search"

    except (PermissionError, ValueError, Exception) as e:
        print(f"[initiate_state] Node routing to failure. Reason: {e}")
        state["error_count"] += 1

        # Keep an empty shell in state if the scraper crashed out completely
        if "target_listing" not in state or not state["target_listing"]:
            state["target_listing"] = {"marketplace_url": url, "title": ""}

        state["next_node"] = "handle_scrape_failure"

    return state


# next try to add feedback loop state to make it self correct
def initiate_keyword_search(state: AgentState) -> AgentState:
    state["current_node"] = "initiate_keyword_search"

    queries = generate_queries(state)

    state["queries"] = queries
    if not queries:
        state["next_node"] = "terminate_with_error"
        return state

    all_discovered_urls = set()
    with DDGS() as ddgs:
        for query in queries[:3]:
            try:
                results = ddgs.text(query, max_results=5)
                for item in results:
                    url = item.get("href")
                    if url:
                        all_discovered_urls.add(url)
            except Exception as e:
                print(f"[search] DDG query failed: {e}")

    # Enforce your limit of up to 10 URLs
    state["urls_to_scrape"] = list(all_discovered_urls)[:10]
    state["normalized_listings"] = []  # Initialize storage array

    state["next_node"] = "process_next_url"
    return state


def process_next_url(state: AgentState) -> dict:  # Note: returning dict, not AgentState
    # 1. Copy the list so we don't mutate the state in place!
    urls = list(state.get("urls_to_scrape", []))

    if not urls:
        return {
            "current_node": "process_next_url",
            "next_node": "analyze_competitors"
            if state.get("normalized_listings")
            else "terminate_with_error",
        }

    current_url = urls.pop(0)

    try:
        listing = extract_and_normalize_amazon(current_url)

        if listing and listing.get("title"):
            # 2. Return ONLY the changes.
            # Because normalized_listings likely has an `add` reducer,
            # returning a list with ONE item tells LangGraph to append it.
            return {
                "current_node": "process_next_url",
                "urls_to_scrape": urls,
                "current_url": current_url,
                "normalized_listings": [
                    NormalizedListing(**listing)
                ],  # Wrap in list to append
                "next_node": "process_next_url",
            }
        else:
            raise ValueError("Empty parse output")

    except Exception as e:
        print(f"[process_next_url] Failed scraping {current_url}: {e}")
        return {
            "current_node": "process_next_url",
            "urls_to_scrape": urls,
            "current_url": current_url,
            "error_count": state.get("error_count", 0)
            + 1,  # Update integer by returning new total
            "next_node": "handle_scrape_failure",
        }


if __name__ == "__main__":
    initial_state = new_state(
        "https://www.amazon.com/Nizoral-Anti-Dandruff-Shampoo-Ketoconazole-Dandruff/dp/B00AINMFAC/"
    )

    compiled_graph = build_graph()
    result = compiled_graph.invoke(initial_state)
