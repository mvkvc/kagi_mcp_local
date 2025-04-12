from contextlib import asynccontextmanager
from dataclasses import dataclass
import os
import subprocess
import socket
from urllib.parse import urlparse
import asyncio

from bs4 import BeautifulSoup
from playwright.async_api import Playwright, Browser, BrowserContext, async_playwright
from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP
from pydantic import Field
import textwrap

load_dotenv()

SELECTOR_RESULTS = "#layout-v2"
SELECTOR_RESULT = "div._0_SRI"
SELECTOR_TITLE = "a.__sri_title_link"
SELECTOR_URL = "a.__sri_title_link"
SELECTOR_SNIPPET = "div.__sri-desc div"


class BrowserManager:
    def __init__(self):
        self.p: Playwright | None = None
        self.browser: Browser | None = None
        self.context: BrowserContext | None = None

    async def startup(
        self, browser_path: str, cdp_url: str, cdp_port: int, page_timeout: int
    ):
        browser_running = False
        parsed_url = urlparse(cdp_url)
        hostname = parsed_url.hostname or "localhost"

        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.settimeout(1)
                result = sock.connect_ex((hostname, cdp_port))
                if result == 0:
                    print(f"Browser already running on {hostname}:{cdp_port}")
                    browser_running = True
        except socket.error as e:
            print(f"Socket check failed: {e}")

        if not browser_running:
            print(f"Starting new browser instance: {browser_path}")
            command = [browser_path, f"--remote-debugging-port={cdp_port}"]
            try:
                subprocess.Popen(
                    command,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                await asyncio.sleep(2)
            except FileNotFoundError:
                print(f"Error: Browser executable not found at {browser_path}")
            except Exception as e:
                print(f"Error starting browser process: {e}")

        self.p = await async_playwright().start()
        self.browser = await self.p.chromium.connect_over_cdp(
            f"{cdp_url}:{cdp_port}", timeout=page_timeout * 2
        )
        if self.browser.contexts:
            self.context = self.browser.contexts[0]
        else:
            print("Warning: No existing browser context found after connecting.")
            raise Exception("Failed to get existing browser context.")

    async def shutdown(self):
        if self.browser:
            await self.browser.close()
        if self.p:
            await self.p.stop()

    @asynccontextmanager
    async def get_browser_page(self, url: str, page_timeout: int):
        page = await self.context.new_page()
        try:
            await page.goto(url, timeout=page_timeout)
            yield page
        finally:
            await page.close()

    async def fetch_content(self, url: str, page_timeout: int):
        async with self.get_browser_page(url, page_timeout) as page:
            content = await page.content()
            bs = BeautifulSoup(content, "html.parser")
            text_content = bs.get_text(strip=True)
            return text_content

    async def fetch_search_results(
        self, queries: list[str], page_timeout: int, results_max: int
    ):
        query_search_results = {}
        fetch_tasks = []
        results_to_update = []

        for query in queries:
            url = f"https://kagi.com/search?q={'+'.join(query.split(' '))}"
            initial_results_for_query = []
            results_count_for_query = 0

            try:
                async with self.get_browser_page(url, page_timeout) as page:
                    await page.wait_for_selector(SELECTOR_RESULTS)
                    results_container = await page.query_selector(SELECTOR_RESULTS)
                    results_elements = await results_container.query_selector_all(
                        SELECTOR_RESULT
                    )

                    for result_element in results_elements:
                        title_element = await result_element.query_selector(
                            SELECTOR_TITLE
                        )
                        title = (
                            await title_element.inner_text() if title_element else None
                        )
                        url_element = await result_element.query_selector(SELECTOR_URL)
                        url = (
                            await url_element.get_attribute("href")
                            if url_element
                            else None
                        )
                        snippet_element = await result_element.query_selector(
                            SELECTOR_SNIPPET
                        )
                        snippet = (
                            await snippet_element.inner_text()
                            if snippet_element
                            else None
                        )

                        if title and url:
                            if results_count_for_query >= results_max:
                                break

                            search_result = SearchResult(
                                title=title, url=url, snippet=snippet, content=None
                            )
                            initial_results_for_query.append(search_result)

                            task = asyncio.create_task(
                                self.fetch_content(url, page_timeout)
                            )
                            fetch_tasks.append(task)
                            results_to_update.append(search_result)
                            results_count_for_query += 1

                query_search_results[query] = initial_results_for_query

            except Exception as e:
                print(f"Error {e} fetching initial search results for {query}")
                query_search_results[query] = []

        if fetch_tasks:
            try:
                fetched_contents = await asyncio.gather(
                    *fetch_tasks, return_exceptions=True
                )
                for i, content_or_exception in enumerate(fetched_contents):
                    if isinstance(content_or_exception, Exception):
                        print(
                            f"Error fetching content for {results_to_update[i].url}: {content_or_exception}"
                        )
                        results_to_update[
                            i
                        ].content = f"Error fetching content: {content_or_exception}"
                    else:
                        results_to_update[i].content = content_or_exception
            except Exception as e:
                print(f"Error during content fetching: {e}")
                for result in results_to_update:
                    if result.content is None:
                        result.content = f"Error gathering content: {e}"

        return query_search_results


@asynccontextmanager
async def lifespan(app: FastMCP):
    app.BROWSER = os.getenv("BROWSER", "/usr/bin/brave-browser")
    app.CDP_URL = os.getenv("CDP_URL", "http://localhost")
    app.CDP_PORT = int(os.getenv("CDP_PORT", "9222"))
    app.PAGE_TIMEOUT = int(os.getenv("PAGE_TIMEOUT", "30000"))
    app.RESULTS_MAX = int(os.getenv("RESULTS_MAX", "10"))
    app.CONTENT_CHAR_LIMIT = int(os.getenv("CONTENT_CHAR_LIMIT", "0"))
    app.browser_manager = BrowserManager()
    await app.browser_manager.startup(
        app.BROWSER, app.CDP_URL, app.CDP_PORT, app.PAGE_TIMEOUT
    )
    yield
    await app.browser_manager.shutdown()


@dataclass
class SearchResult:
    title: str
    url: str
    snippet: str | None = None
    content: str | None = None


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

    app = mcp

    if (
        not app.browser_manager.context
        or not app.browser_manager.browser.is_connected()
    ):
        return "Search error: browser is not connected or context is unavailable."

    if not queries:
        return "Search error: called with no queries."

    try:
        query_search_results = await app.browser_manager.fetch_search_results(
            queries, app.PAGE_TIMEOUT, app.RESULTS_MAX
        )
    except Exception as e:
        return f"Error: {str(e) or repr(e)}"

    if not query_search_results:
        return "No results found."

    return format_search_results(query_search_results, app.CONTENT_CHAR_LIMIT)


def format_search_results(
    search_results: dict[str, list[SearchResult]], content_char_limit: int
) -> str:
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

    for query, results in search_results.items():
        formatted_results = []
        result_counter = 1

        for result in results:
            snippet_display = result.snippet or "No snippet available."
            content_display = "No content fetched."
            if result.content:
                if result.content.startswith(
                    "Error fetching content:"
                ) or result.content.startswith("Error gathering content:"):
                    content_display = result.content
                else:
                    cleaned_content = " ".join(result.content.split())
                    if (
                        content_char_limit > 0
                        and len(cleaned_content) > content_char_limit
                    ):
                        content_display = cleaned_content[:content_char_limit] + "..."
                    else:
                        content_display = cleaned_content

            formatted_results.append(
                result_template.format(
                    result_number=result_counter,
                    title=result.title,
                    url=result.url,
                    snippet=snippet_display,
                    display_content=content_display,
                )
            )
            result_counter += 1

        if not formatted_results:
            formatted_queries.append(
                query_template.format(
                    query=query, formatted_results="No results found for this query."
                )
            )
        else:
            formatted_queries.append(
                query_template.format(
                    query=query, formatted_results="\n\n".join(formatted_results)
                )
            )

    if not formatted_queries:
        return "No results found for any query."

    return "\n\n".join(formatted_queries)


def main():
    mcp.run()


if __name__ == "__main__":
    main()
