# Kernso MCP Server

Resolves natural-language shopping intent into ranked products with structured reasoning via [Model Context Protocol (MCP)](https://modelcontextprotocol.io).

Covers fragrance, wine, boutique hotels, and technical apparel. Best for vibe, occasion, mood, and identity-based queries where keyword search fails.

## Tools

| Tool | Description |
|------|-------------|
| `resolve_intent` | Resolve a shopping query → ranked products with BIS reasoning |
| `list_categories` | Available categories with coverage quality metrics |
| `get_brand_kernel` | Brand positioning, emotional signature, cultural coordinates |
| `explain_ranking` | Why a product ranked where it did |

## Install for Claude Desktop (stdio)

Add to your `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "kernso": {
      "command": "docker",
      "args": [
        "run", "-i", "--rm",
        "-e", "KERNSO_API_URL=https://mcp.kernso.com",
        "kernso/mcp:latest",
        "python", "-m", "kernso_mcp.server", "--stdio"
      ]
    }
  }
}
```

## Install for Cursor / Windsurf (stdio)

Same Docker command as Claude Desktop. Add to your MCP server configuration.

## Use from Claude API / ChatGPT / Perplexity / Gemini (HTTP)

```
Endpoint: https://mcp.kernso.com/mcp
Auth: X-Kernso-Key header
```

## Spec

See [KERNSO_MCP_SPEC.md](docs/KERNSO_MCP_SPEC.md) for the full technical specification.

## License

MIT
