---
name: deep-web-analyst
description: Orchestrates multi-turn deep web searches, sequential link extraction, and cross-reference analysis using built-in browser and search tools.
---

# Deep Web Analyst Skill

Activate this skill when a user asks for alternative product options, replicas, deep price comparisons, or complex investigative searches requiring verification across multiple sources.

## Phase 1: Exhaustive Search Selection
1. Construct a highly specific search query optimized for target indexing (e.g., include keywords like "alternative", "replica", "vs", "review").
2. Execute the native `web_search` tool with a target count of at least `5` results.
3. Analyze the returned list of snippets. Evaluate and rank all 5 URLs based strictly on source authority and query relevance.

## Phase 2: Sequential Deep Content Digging
You must not rely purely on search snippets. You are required to read the actual page content.
1. Target the top 3 to 5 URLs from your ranked list.
2. For *each* URL sequentially, invoke the native `web_fetch` (or `browser`) tool to retrieve the inner web page data.
3. If a page fails to load or returns an error, immediately drop it and move to the next ranked URL in your backlog until you have successfully processed up to 5 sources.

## Phase 3: Extraction & Cross-Reference Cross-Examination
While parsing the extracted page contents, isolate the following metrics:
* **Core Entity Metadata:** Exact product names, manufacturer info, or material specs.
* **Economic Variables:** Base pricing, hidden shipping fees, and vendor reliability indicators.
* **Visual/Aesthetic Properties:** Extract descriptive string matches of the items (e.g., colorways, material finishes, or dimensions) to simulate a conceptual "reverse-image match."

## Phase 4: Structured Synthesis Delivery
Present your final answer using a clean human summary. Your response must include:
1. **The Direct Verdict:** The absolute best alternative or option discovered based on value.
2. **The Source Ledger:** A breakdown summarizing your findings from the extracted sites.
3. **Embed Prevention:** Ensure all raw URLs included in your final response are wrapped inside angle brackets (e.g., `<https://example.com>`) to suppress noisy Discord link previews.