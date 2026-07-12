"""
Node: execute_step

ReAct loop: for the CURRENT plan step only, ask the LLM to produce a concrete
FunctionCall (filling in query/image_url from target_listing), dispatch it,
and append raw results to state. Advances plan_cursor by one per call.

This node gets called repeatedly by the LangGraph loop edge until the plan
is exhausted — it never sees future steps and never re-plans.
"""

import json
import httpx
from states import AgentState, NormalizedListing
from tools import FunctionCall, dispatch, KeywordSearchArgs, ImageSearchArgs

OLLAMA_API_URL = "http://localhost:11434/api/generate"
OLLAMA_MODEL = "qwen3-vl:8b-instruct"


async def node_execute_step(state: AgentState) -> AgentState:
    cursor = state.get("plan_cursor", 0)
    plan = state["execution_plan"]

    if cursor >= len(plan):
        state["next_node"] = "normalize_all"
        return state

    step = plan[cursor]
    state["current_node"] = f"execute_step[{step['step_id']}]"
    strategy = step["strategy"]

    # normalize_listing / validate_listings are handled by dedicated nodes,
    # not the search dispatcher — skip them here, they run after the search loop
    if strategy in ("normalize_listing", "validate_listings", "compare_and_report"):
        state["plan_cursor"] = cursor + 1
        state["next_node"] = "execute_step"
        return state

    listing = NormalizedListing(**state["target_listing"])

    # ── Thought: ask the LLM to turn the plain-language step into a concrete call ──
    prompt = f"""You must produce ONE concrete function call for this research step.

Step: {step["strategy"]}
Rationale: {step["rationale"]}
Input hint: {step["input_hint"]}

Target product:
Title: {listing['title']}
Description: {listing['description'][:500]}
Image URL: {listing['image_urls'][:5] if listing['image_urls'] else "none"}

Rules:
- If strategy is keyword_search: write a clean search query (strip marketing adjectives)
- If strategy is image_search: use the image URL above verbatim
- Return ONLY JSON matching this shape:

For keyword_search:
{{"tool": "keyword_search", "query": "...", "n_results": 10}}

For image_search:
{{"tool": "image_search", "image_url": "...", "n_results": 10}}"""

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                OLLAMA_API_URL,
                json={
                    "model": OLLAMA_MODEL,
                    "prompt": prompt,
                    "stream": False,
                    "format": "json",
                    "options": {"temperature": 0.1},
                },
            )
            resp.raise_for_status()

        raw = resp.json().get("response", "").strip()
        args_dict = json.loads(raw)

        call = FunctionCall(worker_id=f"{step['step_id']}_call", args=args_dict)

        # ── Act ──
        urls = await dispatch(call)

        # ── Observe: write results into state ──
        state["raw_search_results"].append({
            "step_id": step["step_id"],
            "strategy": strategy,
            "query_or_image": args_dict.get("query") or args_dict.get("image_url"),
            "results": urls,
        })

    except Exception as e:
        print(f"[execute_step] Error on {step['step_id']}: {e}")
        state["error_count"] += 1

    state["plan_cursor"] = cursor + 1
    state["next_node"] = "execute_step"
    return state


def should_continue_plan(state: AgentState) -> str:
    """LangGraph conditional edge function."""
    cursor = state.get("plan_cursor", 0)
    if cursor >= len(state["execution_plan"]):
        return "normalize_all"
    return "execute_step"