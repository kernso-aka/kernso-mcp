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
        "results": [
            {
                "product_id": "p1",
                "name": "Portable Fireplace",
                "brand": "D.S. & Durga",
                "category": "fragrance",
                "confidence": 0.85,
                "match_score": 0.85,
                "reasoning": {
                    "intent_match_score": 0.85,
                    "differentiation_vector": "smoky, contemplative",
                },
            },
            {
                "product_id": "p2",
                "name": "Bowmakers",
                "brand": "D.S. & Durga",
                "category": "fragrance",
                "confidence": 0.72,
                "match_score": 0.72,
            },
        ],
        "coverage_flag": "high",
        "category_inferred": "fragrance",
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
    assert data["results"][0]["name"] == "Portable Fireplace"
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


async def test_explain_ranking_invalid_category():
    result = await explain_ranking(query="test", product_id="p1", category="auto")
    assert result.isError
    data = json.loads(result.content[0].text)
    assert data["error"]["code"] == "invalid_category"
