# 🛒 ShopSmart — Autonomous Multi-Agent Price Comparison & Deal Discovery Engine

ShopSmart is a concurrent, multi-agent shopping and deal discovery engine built on **LangGraph**, **Python**, and **Playwright**. It orchestrates distributed web scraping, reverse image matching, and knowledge graph mapping to surface optimal cross-retailer deals based on real-time pricing, proximity logistics, and personalized user constraints.

---

## ⚡ Core Features

- ** LangGraph State Machine**: Orchestrates end-to-end multi-agent execution flows (discovery, scraping, parsing, graph-linking, routing, and ranking).
- **Concurrent Web Extraction**: Parallel browser automation via **Playwright** for real-time catalog ingestion and reverse image search matching.
- **Logistics & Store Routing**: Proximity-aware optimization across storefronts to balance shipping times vs. pickup savings.
- **Personalized Recommendations**: Dynamic constraint filtering taking into account loyalty reward tiers, budget limits, and dietary/profile restrictions.
- **Production Observability**: Full execution tracing, token metering, and sub-graph latency profiling via **LangSmith**.

---