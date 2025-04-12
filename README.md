# kagi_mcp_local

*DISCLAIMER*: This tool is intended for personal use only, as an alternative to manual searching and copy-pasting. It is not designed for automated querying or scraping of Kagi search results. If you have access you should use the official Kagi MCP server at https://github.com/kagisearch/kagimcp.

MCP server for Kagi Search using your local browser.

## Requirements

- [uv](https://docs.astral.sh/uv/getting-started/installation/)

## Setup

```json
{
  "mcpServers": {
    "kagi-search": {
      "command": "uvx",
      "args": [
        "--refresh",
        "--from",
        "git+https://github.com/mvkvc/kagi_mcp_local",
        "kagimcplocal"
      ],
      "env": {
        "BROWSER": "/usr/bin/brave-browser",
        "CDP_URL": "http://localhost",
        "CDP_PORT": "9222",
        "RESULTS_MAX": "10",
        "PAGE_TIMEOUT": "5000"
      }
    }
  }
}
```

## License

[MIT](./LICENSE.md)