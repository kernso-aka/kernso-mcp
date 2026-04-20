"""Entry point for python -m kernso_mcp."""

from kernso_mcp.server import main  # noqa: F401

if __name__ == "__main__":
    import sys
    # Default to stdio if no args
    if len(sys.argv) == 1:
        sys.argv.append("--stdio")
    from kernso_mcp.server import app  # noqa: F811
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--stdio", action="store_true")
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args()

    if args.stdio:
        import asyncio
        from kernso_mcp.server import mcp
        asyncio.run(mcp.run_stdio())
    else:
        import uvicorn
        uvicorn.run(app, host="0.0.0.0", port=args.port)
