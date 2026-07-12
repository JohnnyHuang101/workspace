"""
Concrete tool functions + FunctionCall schema with discriminated union args.
"""

import httpx
from pydantic import BaseModel, Field
from typing import Literal, Union, Annotated, List
from serpapi import GoogleSearch  # pip install google-search-results


SERPAPI_KEY = "5309320e7b1f315807b231dafbad99546a83a6b4fedd222e884fe4335b09eac1"
N_RESULTS = 10


# ──────────────────────────────────────────────
# Args schemas (one per tool)
# ──────────────────────────────────────────────

class KeywordSearchArgs(BaseModel):
    tool: Literal["keyword_search"] = "keyword_search"
    query: str = Field(description="Cleaned search query")
    n_results: int = Field(default=N_RESULTS)

class ImageSearchArgs(BaseModel):
    tool: Literal["image_search"] = "image_search"
    image_url: str = Field(description="Public URL of the product image")
    n_results: int = Field(default=N_RESULTS)

class NormalizeListingArgs(BaseModel):
    tool: Literal["normalize_listing"] = "normalize_listing"
    url: str = Field(description="Product page URL to scrape and normalize")

class ValidateListingsArgs(BaseModel):
    tool: Literal["validate_listings"] = "validate_listings"
    # no extra args — operates on whatever is in state["normalized_listings"]
    # will use LLM on this later

# Discriminated union — Pydantic picks the right model based on the "tool" field
ToolArgs = Annotated[
    Union[KeywordSearchArgs, ImageSearchArgs, NormalizeListingArgs, ValidateListingsArgs],
    Field(discriminator="tool")
]


# ──────────────────────────────────────────────
# FunctionCall — what the planner/executor emits
# ──────────────────────────────────────────────

class FunctionCall(BaseModel):
    worker_id: str = Field(description="e.g. kw_01, img_01, norm_03")
    args: ToolArgs


# ──────────────────────────────────────────────
# Tool implementations
# ──────────────────────────────────────────────

async def keyword_search(args: KeywordSearchArgs) -> List[str]:
    """Return up to n_results product page URLs for a text query."""
    params = {
        "engine": "google_shopping",
        "q": args.query,
        "num": args.n_results,
        "api_key": SERPAPI_KEY,
    }
    search = GoogleSearch(params)
    results = search.get_dict()

    urls = [
        item["link"]
        for item in results.get("shopping_results", [])
        if "link" in item
    ]
    print(f"[keyword_search] '{args.query}' → {len(urls)} results")
    return urls[: args.n_results]


async def image_search(args: ImageSearchArgs) -> List[str]:
    """Return up to n_results product page URLs for a reverse image search."""
    params = {
        "engine": "google_lens",
        "url": args.image_url,
        "num": args.n_results,
        "api_key": SERPAPI_KEY,
    }
    search = GoogleSearch(params)
    results = search.get_dict()

    urls = [
        item["link"]
        for item in results.get("visual_matches", [])
        if "link" in item
    ]
    print(f"[image_search] → {len(urls)} results")
    return urls[: args.n_results]


# ──────────────────────────────────────────────
# Dispatcher — call the right function by args type
# ──────────────────────────────────────────────

async def dispatch(call: FunctionCall) -> List[str]:
    if isinstance(call.args, KeywordSearchArgs):
        return await keyword_search(call.args)
    elif isinstance(call.args, ImageSearchArgs):
        return await image_search(call.args)
    else:
        raise ValueError(f"dispatch() doesn't handle {type(call.args)} — normalize/validate are state-level nodes")
    




import asyncio
import json
from pathlib import Path


async def main():
    state = json.loads(STATE_PATH.read_text())
    state = await node_generate_plan(state)
    print("\nExecution plan written to state:")
    print(json.dumps(state["execution_plan"], indent=2))


    query = f"Amazon similar {state[""]}"
 
asyncio.run(main())