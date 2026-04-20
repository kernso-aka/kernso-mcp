#!/usr/bin/env python3
"""End-to-end stdio transport test.

Spawns the MCP server in stdio mode and exercises the full protocol:
initialize → tools/list → resolve_intent → verify response.

MCP stdio uses newline-delimited JSON (not content-length framing).
"""

import json
import subprocess
import sys
import os
import time
import select


def send_and_receive(proc, msg: dict, timeout_sec: float = 30.0) -> dict | None:
    """Send a JSON-RPC message and read the response (newline-delimited JSON)."""
    line = json.dumps(msg) + "\n"
    proc.stdin.write(line.encode())
    proc.stdin.flush()

    # Read response line(s) - may need to skip notifications
    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        ready = select.select([proc.stdout], [], [], min(remaining, 1.0))
        if ready[0]:
            resp_line = proc.stdout.readline()
            if not resp_line:
                return None
            try:
                resp = json.loads(resp_line.decode().strip())
                # Skip notifications (no id)
                if "id" in resp:
                    return resp
                # It's a notification, keep reading
            except json.JSONDecodeError:
                continue
    return None


def send_notification(proc, msg: dict):
    """Send a notification (no response expected)."""
    line = json.dumps(msg) + "\n"
    proc.stdin.write(line.encode())
    proc.stdin.flush()
    time.sleep(0.2)


def main():
    env = {**os.environ}
    env["KERNSO_API_URL"] = "https://kernso-serve-422143170579.us-east1.run.app"
    env["TELEMETRY_ENABLED"] = "false"

    print("Starting MCP server in stdio mode...")
    proc = subprocess.Popen(
        [sys.executable, "-m", "kernso_mcp.server", "--stdio"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    )

    try:
        time.sleep(1)  # Let server start

        # 1. Initialize
        print("\n=== INITIALIZE ===")
        resp = send_and_receive(proc, {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {"name": "stdio-test", "version": "1.0"},
            },
        })
        assert resp is not None, "No response to initialize"
        assert "result" in resp, f"Initialize failed: {resp}"
        server_info = resp["result"]["serverInfo"]
        print(f"  Server: {server_info['name']}")
        print(f"  Protocol: {resp['result']['protocolVersion']}")
        print("  ✅ Initialize OK")

        # Send initialized notification
        send_notification(proc, {
            "jsonrpc": "2.0",
            "method": "notifications/initialized",
        })

        # 2. List tools
        print("\n=== TOOLS/LIST ===")
        resp = send_and_receive(proc, {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/list",
        })
        assert resp is not None, "No response to tools/list"
        tools = resp["result"]["tools"]
        tool_names = [t["name"] for t in tools]
        print(f"  Tools: {tool_names}")
        assert "resolve_intent" in tool_names, f"Missing resolve_intent in {tool_names}"
        assert "list_categories" in tool_names
        assert "get_brand_kernel" in tool_names
        assert "explain_ranking" in tool_names
        print("  ✅ All 4 tools registered")

        # 3. Call resolve_intent with live query
        print("\n=== RESOLVE_INTENT (live API call) ===")
        resp = send_and_receive(proc, {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": "resolve_intent",
                "arguments": {
                    "query": "a contemplative fragrance for a foggy autumn morning",
                    "category": "fragrance",
                },
            },
        }, timeout_sec=45)
        assert resp is not None, "No response to resolve_intent"
        assert "result" in resp, f"resolve_intent failed: {resp}"
        content = resp["result"]["content"]
        assert len(content) > 0
        data = json.loads(content[0]["text"])
        results = data["results"]
        metadata = data["resolution_metadata"]

        print(f"  Results: {len(results)} products")
        for r in results[:3]:
            print(f"    {r['rank']}. {r['name']} (confidence: {r['confidence']})")
        print(f"  Coverage: {metadata['coverage_flag']}")
        print(f"  Latency: {metadata['latency_ms']}ms")

        assert len(results) >= 1, "No results returned"
        assert metadata["coverage_flag"] in ("high", "partial", "low", "out_of_scope")
        print("  ✅ resolve_intent returns ranked products with coverage_flag")

        # 4. Call list_categories
        print("\n=== LIST_CATEGORIES ===")
        resp = send_and_receive(proc, {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {
                "name": "list_categories",
                "arguments": {},
            },
        })
        assert resp is not None
        data = json.loads(resp["result"]["content"][0]["text"])
        cats = data["categories"]
        print(f"  Categories: {[c['name'] for c in cats]}")
        assert len(cats) >= 4
        print("  ✅ list_categories OK")

        print("\n" + "=" * 60)
        print("ALL STDIO TRANSPORT CHECKS PASSED")
        print("Full round trip: stdio → MCP server → kernso-serve → response")
        print("=" * 60)
        return 0

    except Exception as e:
        print(f"\nFAILED: {e}")
        stderr = proc.stderr.read(4096).decode() if proc.stderr else ""
        if stderr:
            print(f"Server stderr:\n{stderr[:1000]}")
        return 1

    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


if __name__ == "__main__":
    sys.exit(main())
