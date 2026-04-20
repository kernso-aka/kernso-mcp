"""Kernso MCP Server — read-only intent resolution for shopping agents.

Exposes the Kernso Resonance Kernel via MCP (stdio + streamable HTTP).
This is a thin wrapper over the internal resolution REST API.
No graph logic, no ranking code, no secrets in this codebase.

Tools:
  - resolve_intent: Resolve shopping query → ranked products
  - list_categories: Available categories with coverage metrics
  - get_brand_kernel: Brand identity/positioning data
  - explain_ranking: Why a product ranked where it did

Usage:
  # stdio (Claude Desktop, Cursor)
  python -m kernso_mcp.server

  # HTTP (Perplexity, remote agents)
  uvicorn kernso_mcp.server:app --host 0.0.0.0 --port 8080
"""

import contextlib
import json
import logging
import os
import sys
import time
import uuid

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.server import TransportSecuritySettings
from mcp.types import CallToolResult, TextContent, ToolAnnotations
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route

from kernso_schemas import (
    BrandKernelInput,
    Category,
    CategoryInfo,
    CoverageFlag,
    ExplainRankingInput,
    Product,
    ResolveConstraints,
    ResolveIntentInput,
    ResolveIntentOutput,
    ResolutionMetadata,
)
from kernso_mcp_common import (
    setup_logging,
    scrub_pii,
    format_error,
    TelemetryEmitter,
)

# ─── Configuration (all from env vars, never hardcoded) ───

KERNSO_API_URL = os.environ.get(
    "KERNSO_API_URL",
    "https://kernso-serve-422143170579.us-east1.run.app",
)
KERNSO_API_KEY = os.environ.get("KERNSO_API_KEY", "")
MCP_PORT = int(os.environ.get("MCP_PORT", "8080"))
GCP_PROJECT = os.environ.get("GCP_PROJECT")
TELEMETRY_ENABLED = os.environ.get("TELEMETRY_ENABLED", "true").lower() == "true"

# ─── Logging (stderr only — stdout reserved for stdio MCP protocol) ───

logger = setup_logging("kernso-mcp")

# ─── Telemetry ───

telemetry = TelemetryEmitter(
    project_id=GCP_PROJECT,
    enabled=TELEMETRY_ENABLED,
)

# ─── HTTP client (lazy import to avoid circular at module level) ───

import httpx

_READ_ONLY = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    openWorldHint=False,
)


def _api_headers() -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    if KERNSO_API_KEY:
        headers["X-Kernso-Key"] = KERNSO_API_KEY
    return headers


def _make_query_id() -> str:
    return f"mcp-{uuid.uuid4().hex[:12]}"


# ─── MCP Server ───

mcp = FastMCP(
    name="Kernso — Intent Resolution for Shopping",
    instructions=(
        "Kernso resolves natural-language shopping intent into ranked products "
        "with structured reasoning. It covers fragrance, wine, boutique hotels, "
        "and technical apparel. Best for vibe, occasion, mood, and identity-based "
        "queries where keyword search fails.\n\n"
        "Workflow:\n"
        "1. Call list_categories to see what Kernso covers and coverage quality\n"
        "2. Call resolve_intent with a natural-language shopping query\n"
        "3. Optionally call get_brand_kernel to understand a brand's positioning\n"
        "4. Optionally call explain_ranking to understand why a product ranked\n\n"
        "Always check coverage_flag in the response. 'high' = strong signal. "
        "'low' or 'out_of_scope' = Kernso has weak data, consider fallback."
    ),
    stateless_http=True,
    transport_security=TransportSecuritySettings(
        enable_dns_rebinding_protection=False,
    ),
)


# ─── Tool: resolve_intent (spec §4.1) ───

@mcp.tool(
    title="Resolve Shopping Intent",
    annotations=_READ_ONLY,
)
async def resolve_intent(
    query: str,
    category: str = "auto",
    top_k: int = 5,
    max_price_usd: float | None = None,
    min_price_usd: float | None = None,
    exclude_brands: list[str] | None = None,
    include_reasoning: bool = True,
) -> CallToolResult:
    """Resolve a natural-language shopping query into ranked products with
    confidence scores and reasoning.

    Use this when the user expresses preferences, moods, occasions, identity
    signals, or any shopping intent that is not a literal product name.

    Examples:
    - "a fragrance for a rainy autumn evening"
    - "natural wine for a beach picnic with oysters"
    - "hotel in Lisbon that feels like a quiet bookshop"
    - "technical jacket for Tokyo commuting in November"

    Returns up to top_k products (default 5) with structured reasoning.

    Args:
        query: Natural language shopping intent (3-500 chars).
        category: Product category. One of: fragrance, wine, hotel,
            technical_apparel, auto. Use 'auto' to let Kernso infer.
        top_k: Number of ranked products to return (1-20, default 5).
        max_price_usd: Optional maximum price filter.
        min_price_usd: Optional minimum price filter.
        exclude_brands: Optional list of brands to exclude.
        include_reasoning: If true, include BIS-derived reasoning per product.
    """
    query_id = _make_query_id()
    start_ms = time.monotonic() * 1000

    # Validate category
    valid_cats = [c.value for c in Category]
    if category not in valid_cats:
        error = format_error(
            "invalid_category",
            f"Category '{category}' not supported. Valid: {', '.join(valid_cats)}. "
            "Use 'auto' to infer.",
            valid_values=valid_cats,
        )
        return CallToolResult(
            content=[TextContent(type="text", text=json.dumps(error, indent=2))],
            isError=True,
        )

    # Validate query length
    if len(query) < 3:
        error = format_error(
            "query_too_short",
            f"Query must be at least 3 characters. Got {len(query)}.",
        )
        return CallToolResult(
            content=[TextContent(type="text", text=json.dumps(error, indent=2))],
            isError=True,
        )

    if len(query) > 500:
        query = query[:500]

    # PII scrub
    clean_query = scrub_pii(query)

    logger.info(
        "resolve_intent",
        extra={
            "tool": "resolve_intent",
            "query_id": query_id,
            "category": category,
            "top_k": top_k,
        },
    )

    # Build request payload
    payload: dict = {
        "query": clean_query,
        "top_k": top_k,
    }
    # CRITICAL: always pass category through, never default to null
    if category != "auto":
        payload["category"] = category

    if max_price_usd is not None:
        payload.setdefault("constraints", {})["max_price_usd"] = max_price_usd
    if min_price_usd is not None:
        payload.setdefault("constraints", {})["min_price_usd"] = min_price_usd
    if exclude_brands:
        payload.setdefault("constraints", {})["exclude_brands"] = exclude_brands

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{KERNSO_API_URL}/api/resolve",
                headers=_api_headers(),
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()
    except httpx.TimeoutException:
        error = format_error(
            "upstream_unavailable",
            "Kernso resolution engine timed out. Try again in 30s.",
            retryable=True,
        )
        latency_ms = time.monotonic() * 1000 - start_ms
        telemetry.record_tool_call("resolve_intent", "unknown", latency_ms, "error", "timeout")
        return CallToolResult(
            content=[TextContent(type="text", text=json.dumps(error, indent=2))],
            isError=True,
        )
    except httpx.HTTPStatusError as e:
        error = format_error(
            "upstream_unavailable",
            f"Kernso resolution engine returned {e.response.status_code}. Try again in 30s.",
            retryable=True,
        )
        latency_ms = time.monotonic() * 1000 - start_ms
        telemetry.record_tool_call("resolve_intent", "unknown", latency_ms, "error", f"http_{e.response.status_code}")
        return CallToolResult(
            content=[TextContent(type="text", text=json.dumps(error, indent=2))],
            isError=True,
        )

    latency_ms = time.monotonic() * 1000 - start_ms

    # Map response to schema types
    results_raw = data.get("results", data.get("recommendations", []))
    products = []
    for i, p in enumerate(results_raw[:top_k]):
        products.append({
            "product_id": str(p.get("product_id", p.get("id", ""))),
            "name": p.get("name", p.get("product_name", "")),
            "brand": p.get("brand", p.get("brand_name", "")),
            "category": p.get("category", category if category != "auto" else ""),
            "price_usd": p.get("price_usd", p.get("price")),
            "url": p.get("url"),
            "image_url": p.get("image_url", p.get("image")),
            "confidence": p.get("confidence", p.get("match_score", p.get("score", 0))),
            "rank": i + 1,
            "reasoning": _extract_reasoning(p) if include_reasoning else None,
        })

    coverage = data.get("coverage_flag", data.get("resolution_metadata", {}).get("coverage_flag", "partial"))

    output = {
        "results": products,
        "resolution_metadata": {
            "category_inferred": data.get("category_inferred", data.get("category", category)),
            "coverage_flag": coverage,
            "latency_ms": round(latency_ms, 1),
            "graph_version": data.get("graph_version", ""),
        },
    }

    telemetry.record_tool_call("resolve_intent", "unknown", latency_ms, coverage)

    logger.info(
        "resolve_intent_complete",
        extra={
            "tool": "resolve_intent",
            "query_id": query_id,
            "latency_ms": round(latency_ms, 1),
            "result_count": len(products),
            "coverage_flag": coverage,
        },
    )

    return CallToolResult(
        content=[TextContent(type="text", text=json.dumps(output, indent=2))],
    )


def _extract_reasoning(product: dict) -> dict | None:
    """Extract BIS reasoning fields from a product response."""
    reason = product.get("reasoning") or product.get("match_reason")
    if isinstance(reason, dict):
        return reason
    if isinstance(reason, str) and reason:
        return {"differentiation_vector": reason}
    return None


# ─── Tool: list_categories (spec §4.1) ───

@mcp.tool(
    title="List Categories",
    annotations=_READ_ONLY,
)
async def list_categories() -> CallToolResult:
    """Return the list of product categories Kernso currently covers, with
    coverage quality metrics per category.

    Use this once at the start of a session to understand what Kernso can
    and cannot resolve. Cheap call, safe to invoke liberally.
    """
    start_ms = time.monotonic() * 1000

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                f"{KERNSO_API_URL}/api/categories",
                headers=_api_headers(),
            )
            if resp.status_code == 200:
                data = resp.json()
                latency_ms = time.monotonic() * 1000 - start_ms
                telemetry.record_tool_call("list_categories", "unknown", latency_ms, "high")
                return CallToolResult(
                    content=[TextContent(type="text", text=json.dumps(data, indent=2))],
                )
    except Exception:
        pass

    # Fallback: static category list from schema
    latency_ms = time.monotonic() * 1000 - start_ms
    categories = {
        "categories": [
            {
                "name": "fragrance",
                "product_count": 4200,
                "brand_count": 180,
                "coverage_quality": "high",
                "representative_brands": [
                    "D.S. & Durga", "Ellis Brooklyn", "Amouage",
                    "Goldfield & Banks", "Juliette Has a Gun",
                ],
                "typical_query_patterns": ["scent profile", "occasion", "mood", "season"],
            },
            {
                "name": "wine",
                "product_count": 2300,
                "brand_count": 197,
                "coverage_quality": "partial",
                "representative_brands": [],
                "typical_query_patterns": ["pairing", "region", "occasion", "style"],
            },
            {
                "name": "hotel",
                "product_count": 4000,
                "brand_count": 46,
                "coverage_quality": "partial",
                "representative_brands": [],
                "typical_query_patterns": ["vibe", "location", "occasion", "style"],
            },
            {
                "name": "technical_apparel",
                "product_count": 2900,
                "brand_count": 307,
                "coverage_quality": "partial",
                "representative_brands": [],
                "typical_query_patterns": ["activity", "climate", "style", "function"],
            },
        ]
    }

    telemetry.record_tool_call("list_categories", "unknown", latency_ms, "high")
    return CallToolResult(
        content=[TextContent(type="text", text=json.dumps(categories, indent=2))],
    )


# ─── Tool: get_brand_kernel (spec §4.1) ───

@mcp.tool(
    title="Get Brand Kernel",
    annotations=_READ_ONLY,
)
async def get_brand_kernel(
    brand_name: str,
    category_hint: str = "auto",
) -> CallToolResult:
    """Return the structured brand kernel for a known brand — its positioning,
    emotional signature, occasion targets, differentiation vectors, and
    cultural coordinates.

    Use this when the user asks about a specific brand, compares brands, or
    when you need to explain WHY a product was ranked as it was.

    Returns null if brand is not in Kernso's coverage.

    Args:
        brand_name: The brand name to look up (min 2 chars).
        category_hint: Optional category hint (fragrance, wine, hotel,
            technical_apparel, auto).
    """
    start_ms = time.monotonic() * 1000

    if len(brand_name) < 2:
        error = format_error(
            "query_too_short",
            f"Brand name must be at least 2 characters. Got {len(brand_name)}.",
        )
        return CallToolResult(
            content=[TextContent(type="text", text=json.dumps(error, indent=2))],
            isError=True,
        )

    clean_name = scrub_pii(brand_name)
    slug = clean_name.lower().replace(" ", "-").replace("&", "and").replace(".", "")

    logger.info(
        "get_brand_kernel",
        extra={"tool": "get_brand_kernel", "brand": clean_name},
    )

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                f"{KERNSO_API_URL}/api/brands/{slug}",
                headers=_api_headers(),
            )
            if resp.status_code == 200:
                data = resp.json()
                latency_ms = time.monotonic() * 1000 - start_ms
                telemetry.record_tool_call("get_brand_kernel", "unknown", latency_ms, "high")
                return CallToolResult(
                    content=[TextContent(type="text", text=json.dumps(data, indent=2))],
                )
            elif resp.status_code == 404:
                latency_ms = time.monotonic() * 1000 - start_ms
                telemetry.record_tool_call("get_brand_kernel", "unknown", latency_ms, "out_of_scope")
                result = {
                    "brand_kernel": None,
                    "coverage_flag": "out_of_scope",
                    "suggestion": "Try list_categories to see covered brands.",
                }
                return CallToolResult(
                    content=[TextContent(type="text", text=json.dumps(result, indent=2))],
                )
    except Exception as e:
        logger.warning("get_brand_kernel failed", extra={"error": str(e)})

    latency_ms = time.monotonic() * 1000 - start_ms
    error = format_error(
        "upstream_unavailable",
        "Kernso resolution engine temporarily unavailable. Try again in 30s.",
        retryable=True,
    )
    telemetry.record_tool_call("get_brand_kernel", "unknown", latency_ms, "error", "upstream")
    return CallToolResult(
        content=[TextContent(type="text", text=json.dumps(error, indent=2))],
        isError=True,
    )


# ─── Tool: explain_ranking (spec §4.1) ───

@mcp.tool(
    title="Explain Ranking",
    annotations=_READ_ONLY,
)
async def explain_ranking(
    query: str,
    product_id: str,
    category: str,
) -> CallToolResult:
    """Given a query and a specific product_id (from a prior resolve_intent
    call), return a detailed explanation of why that product ranked where it
    did — which edges fired, which discourse signals contributed, which brand
    kernel attributes matched.

    Use this when the user asks "why this one?" or when an agent needs to
    justify a recommendation.

    Args:
        query: The original shopping query.
        product_id: Product ID from a prior resolve_intent result.
        category: Product category (required, not auto). One of: fragrance,
            wine, hotel, technical_apparel.
    """
    start_ms = time.monotonic() * 1000

    # Category is required and cannot be auto for explain
    valid_cats = ["fragrance", "wine", "hotel", "technical_apparel"]
    if category not in valid_cats:
        error = format_error(
            "invalid_category",
            f"Category must be one of {valid_cats} for explain_ranking (not 'auto').",
            valid_values=valid_cats,
        )
        return CallToolResult(
            content=[TextContent(type="text", text=json.dumps(error, indent=2))],
            isError=True,
        )

    clean_query = scrub_pii(query)

    logger.info(
        "explain_ranking",
        extra={"tool": "explain_ranking", "product_id": product_id, "category": category},
    )

    try:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.post(
                f"{KERNSO_API_URL}/api/explain",
                headers=_api_headers(),
                json={
                    "query": clean_query,
                    "product_id": product_id,
                    "category": category,
                },
            )
            if resp.status_code == 200:
                data = resp.json()
                latency_ms = time.monotonic() * 1000 - start_ms
                telemetry.record_tool_call("explain_ranking", "unknown", latency_ms, "high")
                return CallToolResult(
                    content=[TextContent(type="text", text=json.dumps(data, indent=2))],
                )
    except Exception as e:
        logger.warning("explain_ranking failed", extra={"error": str(e)})

    latency_ms = time.monotonic() * 1000 - start_ms
    error = format_error(
        "upstream_unavailable",
        "Kernso resolution engine temporarily unavailable. Try again in 30s.",
        retryable=True,
    )
    telemetry.record_tool_call("explain_ranking", "unknown", latency_ms, "error", "upstream")
    return CallToolResult(
        content=[TextContent(type="text", text=json.dumps(error, indent=2))],
        isError=True,
    )


# ─── ASGI App (for uvicorn / Cloud Run) ───

async def health(request):
    return JSONResponse({
        "status": "ok",
        "service": "kernso-mcp",
        "version": "0.1.0",
        "tools": ["resolve_intent", "list_categories", "get_brand_kernel", "explain_ranking"],
    })


@contextlib.asynccontextmanager
async def lifespan(a):
    async with mcp.session_manager.run():
        logger.info("MCP session manager started")
        yield
        logger.info("MCP session manager stopped")


_mcp_app = mcp.streamable_http_app()

app = Starlette(
    routes=[
        Route("/health", health),
        Mount("/", _mcp_app),
    ],
    lifespan=lifespan,
)


# ─── Stdio entry point ───

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Kernso MCP Server")
    parser.add_argument("--stdio", action="store_true", help="Run in stdio mode")
    parser.add_argument("--port", type=int, default=MCP_PORT, help="HTTP port")
    args = parser.parse_args()

    if args.stdio:
        mcp.run(transport="stdio")
    else:
        import uvicorn
        logger.info(
            "Starting Kernso MCP server",
            extra={"port": args.port, "api_url": KERNSO_API_URL},
        )
        uvicorn.run(app, host="0.0.0.0", port=args.port)
