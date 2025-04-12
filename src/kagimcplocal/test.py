import asyncio
import argparse
import os

from kagimcplocal.server import BrowserManager, format_search_results


async def main():
    parser = argparse.ArgumentParser(
        description="Test script for Kagi MCP local search."
    )
    parser.add_argument(
        "--queries", nargs="+", required=True, help="Search queries to execute."
    )
    parser.add_argument(
        "--browser",
        default=os.getenv("BROWSER", "/usr/bin/brave-browser"),
        help="Path to the browser executable.",
    )
    parser.add_argument(
        "--cdp-url",
        default=os.getenv("CDP_URL", "http://localhost"),
        help="URL for Chrome DevTools Protocol.",
    )
    parser.add_argument(
        "--cdp-port",
        type=int,
        default=int(os.getenv("CDP_PORT", "9222")),
        help="Port for Chrome DevTools Protocol.",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=int(os.getenv("PAGE_TIMEOUT", "10000")),
        help="Page load timeout in milliseconds.",
    )
    parser.add_argument(
        "--max-results",
        type=int,
        default=int(os.getenv("RESULTS_MAX", "10")),
        help="Maximum number of search results per query.",
    )
    parser.add_argument(
        "--content-limit",
        type=int,
        default=int(os.getenv("CONTENT_CHAR_LIMIT", "0")),
        help="Maximum characters for fetched content display (0 for no limit).",
    )
    args = parser.parse_args()
    browser_manager = BrowserManager()
    await browser_manager.startup(
        args.browser, args.cdp_url, args.cdp_port, args.timeout
    )
    query_search_results = await browser_manager.fetch_search_results(
        args.queries, args.timeout, args.max_results
    )
    if len(query_search_results) > 0:
        print(format_search_results(query_search_results, args.content_limit))
    else:
        print("No results found.")

    await browser_manager.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
