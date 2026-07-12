"""
Node: generate_plan

Produces a high-level research plan as an ordered list of plain-language steps.
Concrete tool calls (which URLs to normalize, how many image results came back, etc.)
are resolved dynamically by later nodes — this node only decides *strategy*.
"""

import json
import httpx
from pathlib import Path
from pydantic import BaseModel, Field
from typing import List, Literal
from states import NormalizedListing, AgentState

OLLAMA_API_URL = "http://localhost:11434/api/generate"
OLLAMA_MODEL = "qwen2.5:32b-instruct"


# ──────────────────────────────────────────────
# Schema
# ──────────────────────────────────────────────

class PlanStep(BaseModel):
    step_id: str = Field(description="Sequential ID, e.g. step_01, step_02")
    strategy: Literal["keyword_search", "image_search", "normalize_listing", "validate_listings", "compare_and_report"]
    rationale: str = Field(description="Why this step is in the plan")
    input_hint: str = Field(description="What this step will receive as input, described in plain language")


class ExecutionPlan(BaseModel):
    steps: List[PlanStep]


# ──────────────────────────────────────────────
# Node
# ──────────────────────────────────────────────

async def node_generate_plan(state: AgentState) -> AgentState:
    state["current_node"] = "generate_plan"
    state["next_node"] = "execute_plan"

    listing = NormalizedListing(**state["target_listing"])

    system_prompt = """You are an expert e-commerce research planner.

Given a target product listing, produce a high-level research strategy as an ordered list of steps.

Available strategies:
- keyword_search: search for similar products by text query (use cleaned title, strip sales adjectives like "Premium", "Hot Deal", "Best")
- image_search: search using the product image URL to find visually similar listings
- normalize_listing: scrape and structure a raw product URL into a NormalizedListing
- validate_listings: verify that collected listings actually match the target product
- compare_and_report: produce a final price/condition comparison report

Rules:
- Do NOT specify exact URLs, query strings, or counts — those are resolved at runtime
- DO decide which strategies to use and in what order
- If the product has images, include an image_search step
- Always end with validate_listings then compare_and_report
- Return ONLY valid JSON matching the schema, no markdown fences"""

    user_prompt = f"""Target product:
Title: {listing['title']}
Description: {listing['description']}
Image URLs available: {len(listing['image_urls']) > 0}

Return a JSON object with this exact shape:
{{
  "steps": [
    {{
      "step_id": "step_01",
      "strategy": "keyword_search",
      "rationale": "...",
      "input_hint": "..."
    }}
  ]
}}"""

    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(
                OLLAMA_API_URL,
                json={
                    "model": OLLAMA_MODEL,
                    "prompt": f"{system_prompt}\n\n{user_prompt}",
                    "stream": False,
                    "format": "json",
                    "options": {"temperature": 0.1},
                },
            )
            resp.raise_for_status()

        raw = resp.json().get("response", "").strip()

        plan = ExecutionPlan.model_validate_json(raw)
        state["execution_plan"] = [step.model_dump() for step in plan.steps]

        # Persist next to state.json
        run_dir = Path("./agent_runs") / state["run_id"]
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "execution_plan.json").write_text(
            json.dumps(state["execution_plan"], indent=2)
        )
        print(f"[generate_plan] {len(plan.steps)} steps planned")
        for step in plan.steps:
            print(f"  {step.step_id}: {step.strategy} — {step.rationale}")

    except Exception as e:
        print(f"[generate_plan] Error: {e}")
        state["error_count"] += 1

    return state


import asyncio
import json
from pathlib import Path

STATE_PATH = Path(r"C:\Users\Owner\GitHub\ai-ml-foundations-book-collection\shopping_agent\agent_runs\20260630_193733_69a499\state.json")
 
async def main():
    state = json.loads(STATE_PATH.read_text())
    state = await node_generate_plan(state)
    print("\nExecution plan written to state:")
    print(json.dumps(state["execution_plan"], indent=2))
 
asyncio.run(main())