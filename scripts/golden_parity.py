#!/usr/bin/env python3
"""Golden dataset parity test: MCP server vs direct REST API.

Runs all golden queries through both paths and compares results.
MRR delta must be zero — MCP layer is a thin wrapper.

Usage:
    python scripts/golden_parity.py --mcp-url http://localhost:9200 \
        --api-url https://kernso-serve-422143170579.us-east1.run.app
"""

import argparse
import json
import math
import sys
import time
import urllib.request
from pathlib import Path


GOLDEN_PATH = Path("/Users/mainakmazumdar/kernso/golden_dataset/golden_dataset_v2_consensus.json")


def call_direct_api(query: str, api_url: str) -> list:
    """Call the resolution API directly."""
    try:
        req = urllib.request.Request(
            f"{api_url}/api/resolve",
            data=json.dumps({"query": query}).encode(),
            headers={"Content-Type": "application/json"},
        )
        resp = urllib.request.urlopen(req, timeout=30)
        data = json.loads(resp.read())
        return data.get("results", [])
    except Exception as e:
        sys.stderr.write(f"  API error: {e}\n")
        return []


def call_mcp(query: str, mcp_url: str, session_id: str = "") -> list:
    """Call resolve_intent via MCP protocol."""
    try:
        # MCP tools/call
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "resolve_intent",
                "arguments": {"query": query, "top_k": 10},
            },
        }
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if session_id:
            headers["Mcp-Session-Id"] = session_id

        req = urllib.request.Request(
            f"{mcp_url}/mcp",
            data=json.dumps(payload).encode(),
            headers=headers,
        )
        resp = urllib.request.urlopen(req, timeout=45)
        raw = resp.read().decode()

        # Parse SSE response
        for line in raw.split("\n"):
            if line.startswith("data: "):
                data = json.loads(line[6:])
                if "result" in data:
                    content = data["result"].get("content", [])
                    if content and content[0].get("text"):
                        parsed = json.loads(content[0]["text"])
                        return parsed.get("results", [])
        return []
    except Exception as e:
        sys.stderr.write(f"  MCP error: {e}\n")
        return []


def mcp_init(mcp_url: str) -> str:
    """Initialize MCP session, return session ID."""
    payload = {
        "jsonrpc": "2.0",
        "id": 0,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-03-26",
            "capabilities": {},
            "clientInfo": {"name": "parity-test", "version": "1.0"},
        },
    }
    req = urllib.request.Request(
        f"{mcp_url}/mcp",
        data=json.dumps(payload).encode(),
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        },
    )
    resp = urllib.request.urlopen(req, timeout=15)
    raw = resp.read().decode()
    for line in raw.split("\n"):
        if line.startswith("data: "):
            data = json.loads(line[6:])
            return data.get("result", {}).get("sessionId", "")
    return ""


def product_match(resolved: str, golden: str) -> bool:
    r, g = resolved.lower().strip(), golden.lower().strip()
    if r == g or g in r or r in g:
        return True
    if " - " in r:
        short = r.split(" - ", 1)[1]
        if short == g or g in short or short in g:
            return True
    if " - " in g:
        short = g.split(" - ", 1)[1]
        if short == r or r in short or short in r:
            return True
    return False


def reciprocal_rank(results: list, golden_products: list, name_key: str = "name") -> float:
    for i, r in enumerate(results):
        rname = r.get(name_key, r.get("product_name", ""))
        for gp in golden_products:
            if product_match(rname, gp):
                return 1.0 / (i + 1)
    return 0.0


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mcp-url", default="http://localhost:9200")
    parser.add_argument("--api-url", default="https://kernso-serve-422143170579.us-east1.run.app")
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()

    golden = json.loads(GOLDEN_PATH.read_text())
    if args.limit > 0:
        golden = golden[:args.limit]

    sys.stderr.write(f"Running {len(golden)} golden queries...\n")

    # Init MCP session
    session_id = mcp_init(args.mcp_url)
    sys.stderr.write(f"MCP session: {session_id[:20]}...\n")

    api_rrs = []
    mcp_rrs = []
    mismatches = []

    for i, entry in enumerate(golden):
        query = entry.get("query_text", entry.get("query", ""))
        golden_products_raw = entry.get("golden_products", entry.get("expected_products", []))
        golden_products = []
        for gp in golden_products_raw:
            if isinstance(gp, dict):
                golden_products.append(gp.get("product_name", gp.get("name", "")))
            else:
                golden_products.append(str(gp))

        if not golden_products or not query:
            continue

        # Direct API
        api_results = call_direct_api(query, args.api_url)
        api_rr = reciprocal_rank(api_results, golden_products, name_key="product_name")
        api_rrs.append(api_rr)

        # MCP (uses "name" key in response)
        mcp_results = call_mcp(query, args.mcp_url, session_id)
        mcp_rr = reciprocal_rank(mcp_results, golden_products, name_key="name")
        mcp_rrs.append(mcp_rr)

        if abs(api_rr - mcp_rr) > 0.001:
            mismatches.append({
                "query": query,
                "api_rr": api_rr,
                "mcp_rr": mcp_rr,
                "delta": mcp_rr - api_rr,
            })

        if (i + 1) % 10 == 0:
            sys.stderr.write(f"  {i+1}/{len(golden)} done\n")

        # Small delay to avoid rate limiting
        time.sleep(0.2)

    api_mrr = sum(api_rrs) / len(api_rrs) if api_rrs else 0
    mcp_mrr = sum(mcp_rrs) / len(mcp_rrs) if mcp_rrs else 0
    delta = mcp_mrr - api_mrr

    report = {
        "total_queries": len(golden),
        "api_mrr": round(api_mrr, 4),
        "mcp_mrr": round(mcp_mrr, 4),
        "delta": round(delta, 4),
        "mismatches": len(mismatches),
        "mismatch_details": mismatches[:10],  # first 10
        "verdict": "PASS" if abs(delta) < 0.001 else "FAIL — MRR delta non-zero",
    }

    print(json.dumps(report, indent=2))
    return 0 if abs(delta) < 0.001 else 1


if __name__ == "__main__":
    sys.exit(main())
