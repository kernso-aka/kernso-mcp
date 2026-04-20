"""Tests for Kernso MCP server tools."""

import json
import pytest
import respx
import httpx

# Patch env before importing server
import os
os.environ["KERNSO_API_URL"] = "https://test-api.kernso.com"
os.environ["KERNSO_API_KEY"] = "test-key"
os.environ["TELEMETRY_ENABLED"] = "false"

from kernso_mcp.server import resolve_intent, list_categories, get_brand_kernel, explain_ranking


def _mock_resolve_response():
    return {
        "query": "smoky fragrance",
        "query_id": "test123",
        "results": [
            {
                "product_name": "D.S. & Durga - Portable Fireplace",
                "brand": "D.S. & Durga",
                "handle": "portable-fireplace",
                "score": 1.0,
                "provenance": {
                    "sources": [{"source": "graph", "score": 1.0}],
                    "path_count": 1,
                    "query_type": "vibe",
                    "discourse_boost": 0.05,
                },
                "kernel_delta": 0.15,
                "kernel_score": 1.0,
                "kernel_reasoning": [
                    "identity[primary] +0.10: smoky_contemplative \u2190 smoky woodfire leather",
                ],
                "identity_signal_strength": 1.0,
            },
            {
                "product_name": "D.S. & Durga - Bowmakers",
                "brand": "D.S. & Durga",
                "handle": "bowmakers",
                "score": 0.72,
                "provenance": {"sources": [{"source": "bm25", "score": 0.72}], "path_count": 1, "query_type": "vibe"},
                "kernel_delta": 0.0,
                "kernel_score": 0.72,
                "kernel_reasoning": [],
                "identity_signal_strength": 0.8,
            },
        ],
        "coverage_flag": "high",
        "category_inferred": "fragrance",
        "latency_ms": 250,
    }


@respx.mock
async def test_resolve_intent_success():
    respx.post("https://test-api.kernso.com/api/resolve").mock(
        return_value=httpx.Response(200, json=_mock_resolve_response())
    )
    result = await resolve_intent(query="smoky fragrance for autumn evenings")
    assert not result.isError
    data = json.loads(result.content[0].text)
    assert len(data["results"]) == 2
    assert data["results"][0]["name"] == "D.S. & Durga - Portable Fireplace"
    assert data["results"][0]["product_id"] == "portable-fireplace"
    assert data["results"][0]["reasoning"] is not None
    assert data["results"][0]["reasoning"]["intent_match_score"] == 1.0
    assert "smoky_contemplative" in data["results"][0]["reasoning"]["occasion_fit"]
    assert data["results"][0]["reasoning"]["provenance"]["edge_types"] == ["graph"]
    assert data["resolution_metadata"]["coverage_flag"] == "high"


@respx.mock
async def test_resolve_intent_invalid_category():
    result = await resolve_intent(query="test query", category="cars")
    assert result.isError
    data = json.loads(result.content[0].text)
    assert data["error"]["code"] == "invalid_category"


async def test_resolve_intent_query_too_short():
    result = await resolve_intent(query="ab")
    assert result.isError
    data = json.loads(result.content[0].text)
    assert data["error"]["code"] == "query_too_short"


@respx.mock
async def test_resolve_intent_timeout():
    respx.post("https://test-api.kernso.com/api/resolve").mock(
        side_effect=httpx.TimeoutException("timeout")
    )
    result = await resolve_intent(query="test query for timeout")
    assert result.isError
    data = json.loads(result.content[0].text)
    assert data["error"]["code"] == "upstream_unavailable"
    assert data["error"]["retryable"] is True


@respx.mock
async def test_resolve_intent_pii_scrubbed():
    """Verify PII in queries is scrubbed before sending to API."""
    route = respx.post("https://test-api.kernso.com/api/resolve").mock(
        return_value=httpx.Response(200, json=_mock_resolve_response())
    )
    await resolve_intent(query="fragrance for user@example.com evening")
    sent_body = json.loads(route.calls[0].request.content)
    assert "user@example.com" not in sent_body["query"]
    assert "[REDACTED_EMAIL]" in sent_body["query"]


@respx.mock
async def test_resolve_intent_category_passthrough():
    """Category must always be passed to the API, never defaulting to null."""
    route = respx.post("https://test-api.kernso.com/api/resolve").mock(
        return_value=httpx.Response(200, json=_mock_resolve_response())
    )
    await resolve_intent(query="smoky autumn fragrance", category="fragrance")
    sent_body = json.loads(route.calls[0].request.content)
    assert sent_body["category"] == "fragrance"


async def test_list_categories_fallback():
    """list_categories returns static fallback when API is unreachable."""
    result = await list_categories()
    assert not result.isError
    data = json.loads(result.content[0].text)
    assert "categories" in data
    assert len(data["categories"]) == 4
    names = [c["name"] for c in data["categories"]]
    assert "fragrance" in names


@respx.mock
async def test_get_brand_kernel_not_found():
    respx.get("https://test-api.kernso.com/api/brands/nonexistent-brand").mock(
        return_value=httpx.Response(404)
    )
    result = await get_brand_kernel(brand_name="Nonexistent Brand")
    assert not result.isError
    data = json.loads(result.content[0].text)
    assert data["brand_kernel"] is None
    assert data["coverage_flag"] == "out_of_scope"


async def test_get_brand_kernel_name_too_short():
    result = await get_brand_kernel(brand_name="X")
    assert result.isError
    data = json.loads(result.content[0].text)
    assert data["error"]["code"] == "query_too_short"


@respx.mock
async def test_product_id_always_populated():
    """Spec §4.1: product_id is REQUIRED. Must never be empty."""
    respx.post("https://test-api.kernso.com/api/resolve").mock(
        return_value=httpx.Response(200, json=_mock_resolve_response())
    )
    result = await resolve_intent(query="smoky fragrance for autumn")
    data = json.loads(result.content[0].text)
    for product in data["results"]:
        assert product["product_id"], f"Empty product_id for {product['name']}"
        assert len(product["product_id"]) > 0


@respx.mock
async def test_reasoning_populated_when_requested():
    """Spec §4.1: reasoning must be present when include_reasoning=True (default)."""
    respx.post("https://test-api.kernso.com/api/resolve").mock(
        return_value=httpx.Response(200, json=_mock_resolve_response())
    )
    result = await resolve_intent(query="smoky fragrance for autumn")
    data = json.loads(result.content[0].text)
    # First result has kernel_reasoning data — must be populated
    r0 = data["results"][0]["reasoning"]
    assert r0 is not None, "Reasoning must not be None when API returns kernel data"
    assert "intent_match_score" in r0
    assert "provenance" in r0
    assert "edge_types" in r0["provenance"]


async def test_explain_ranking_invalid_category():
    result = await explain_ranking(query="test", product_id="p1", category="auto")
    assert result.isError
    data = json.loads(result.content[0].text)
    assert data["error"]["code"] == "invalid_category"
