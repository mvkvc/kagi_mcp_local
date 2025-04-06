import asyncio
import logging
import os
import subprocess
import textwrap
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import AsyncGenerator

from bs4 import BeautifulSoup
from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP
from playwright.async_api import (
    Browser,
    BrowserContext,
    Page,
    async_playwright,
)
from pydantic import Field


logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)

load_dotenv()

BROWSER = os.getenv("BROWSER", "/usr/bin/brave-browser")
CDP_URL = os.getenv("CDP_URL", "http://localhost")
CDP_PORT = int(os.getenv("CDP_PORT", "9222"))
PAGE_TIMEOUT = int(os.getenv("PAGE_TIMEOUT", "5000"))
RESULTS_MAX = int(os.getenv("RESULTS_MAX", "10"))

SELECTOR_RESULTS = "#layout-v2 > div:nth-child(2)"
SELECTOR_TITLE = "a.__sri_title_link"
SELECTOR_URL = "a.__sri_title_link"
SELECTOR_SNIPPET = "div.__sri-desc div"

process: subprocess.Popen | None = None
browser: Browser | None = None
context: BrowserContext | None = None


@dataclass
class SearchResult:
    title: str
    url: str
    snippet: str | None = None
    content: str | None = None


@asynccontextmanager
async def get_browser_page(
    context: BrowserContext, url: str
) -> AsyncGenerator[Page, None]:
    logger.debug(f"Opening new page for URL: {url}")
    page = await context.new_page()
    try:
        await page.goto(url, timeout=PAGE_TIMEOUT)
        logger.debug(f"Successfully navigated to {url}")
        yield page
    finally:
        logger.debug("Closing page")
        await page.close()


async def fetch_content(context: BrowserContext, url: str) -> str:
    logger.debug(f"Fetching content from {url}")
    async with get_browser_page(context, url) as page:
        content = await page.content()
        bs = BeautifulSoup(content, "html.parser")
        text_content = bs.get_text(strip=True)
        logger.debug(f"Successfully fetched and parsed content from {url}")
        return text_content


@asynccontextmanager
async def lifespan(_: FastMCP):
    global process, browser, context
    browser_running = (
        subprocess.run(
            [
                "pgrep",
                "-f",
                f"{BROWSER.split('/')[-1]}.*remote-debugging-port={CDP_PORT}",
            ],
            capture_output=True,
            text=True,
        ).returncode
        == 0
    )

    if not browser_running:
        logger.info(f"Starting browser process with CDP port {CDP_PORT}")
        process = subprocess.Popen(
            f'"{BROWSER}" --remote-debugging-port={CDP_PORT}',
            shell=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    else:
        logger.info("Browser already running with CDP enabled")

    try:
        logger.info("Connecting to browser via CDP")
        async with async_playwright() as p:
            browser = await p.chromium.connect_over_cdp(
                f"{CDP_URL}:{CDP_PORT}", timeout=PAGE_TIMEOUT
            )
            context = browser.contexts[0]
            logger.info("Successfully connected to browser")
            yield context
    except Exception as e:
        logger.error(f"Error during browser connection: {str(e)}")
        raise
    finally:
        logger.info("Cleaning up browser resources")
        if context:
            await context.close()
        if browser:
            await browser.close()
        if process:
            process.kill()


mcp = FastMCP(
    "kagimcp",
    dependencies=[
        "mcp[cli]",
        "playwright",
        "beautifulsoup4",
        "python-dotenv",
        "pydantic",
    ],
    lifespan=lifespan,
)


@mcp.tool()
async def kagi_search_fetch(
    queries: list[str] = Field(
        description="One or more concise, keyword-focused search queries. Include essential context within each query for standalone use."
    ),
) -> str:
    """Fetch web results based on one or more queries using the Kagi Search API. Use for general search and when the user explicitly tells you to 'fetch' results/information. Results are from all queries given. They are numbered continuously, so that a user may be able to refer to a result by a specific number."""
    try:
        if not queries:
            raise ValueError("Search called with no queries.")

        logger.info(f"Starting search with {len(queries)} queries")
        search_results = {query: [] for query in queries}

        for query in queries:
            url = f"https://kagi.com/search?q={'+'.join(query.split(' '))}"
            logger.info(f"Processing query: {query}")

            try:
                async with get_browser_page(context, url) as page:
                    await page.wait_for_selector(SELECTOR_RESULTS, timeout=PAGE_TIMEOUT)
                    result_elements = await page.query_selector_all(SELECTOR_RESULTS)
                    logger.info(
                        f"Found {len(result_elements)} results for query: {query}"
                    )

                    for idx, result in enumerate(result_elements[:RESULTS_MAX]):
                        try:
                            url_element = await result.query_selector(SELECTOR_URL)
                            url = (
                                await url_element.get_attribute("href")
                                if url_element
                                else None
                            )
                            title_element = await result.query_selector(SELECTOR_TITLE)
                            title = (
                                await title_element.inner_text()
                                if title_element
                                else None
                            )
                            snippet_element = await result.query_selector(
                                SELECTOR_SNIPPET
                            )
                            snippet = (
                                await snippet_element.inner_text()
                                if snippet_element
                                else None
                            )

                            if url and title:
                                search_results[query].append(
                                    SearchResult(
                                        title=title,
                                        url=url,
                                        snippet=snippet,
                                    )
                                )
                                logger.debug(f"Processed result {idx + 1}: {title}")
                            else:
                                logger.warning(
                                    f"Skipping result {idx + 1} due to missing title or URL"
                                )
                        except Exception as e:
                            logger.error(f"Error processing result {idx + 1}: {str(e)}")
                            continue
            except Exception as e:
                logger.error(f"Error processing query '{query}': {str(e)}")
                continue

        logger.info("Fetching content for all results")
        for query, results in search_results.items():
            await asyncio.gather(
                *[fetch_content(context, result.url) for result in results]
            )

        logger.info("Search completed successfully")
        return format_search_results(search_results)

    except Exception as e:
        logger.error(f"Fatal search error: {str(e)}")
        return f"Search error: {str(e)}"


def format_search_results(search_results: dict[str, list[SearchResult]]) -> str:
    logger.debug("Formatting search results")
    result_template = textwrap.dedent("""
        {result_number}: {title}
        URL: {url}
        Content: {display_content}
    """).strip()

    query_template = textwrap.dedent("""
        -----
        Results for search query "{query}":
        -----
        {formatted_results}
    """).strip()

    formatted_queries = []
    result_counter = 1

    for query, results in search_results.items():
        formatted_results = []

        for result in results:
            display_content = result.content or result.snippet or "No content available"
            formatted_results.append(
                result_template.format(
                    result_number=result_counter,
                    title=result.title,
                    url=result.url,
                    display_content=display_content,
                )
            )
            result_counter += 1

        formatted_queries.append(
            query_template.format(
                query=query, formatted_results="\n\n".join(formatted_results)
            )
        )

    return "\n\n".join(formatted_queries)


def main():
    mcp.run()


if __name__ == "__main__":
    main()
