"""Crawl documentation pages with R2 caching.

Supports three ingestion modes:
1. Batch: predefined source lists (base_url + paths)
2. Single URL: ingest any arbitrary web page
3. Cached: skip crawling, use R2-stored content

On every crawl, raw HTML is stored in R2 so subsequent runs are instant.
"""

import logging
import time
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup, Tag

from app.config import settings
from app.storage import store_document, get_document
from app.telemetry import get_tracer

logger = logging.getLogger(__name__)


@dataclass
class RawDocument:
    url: str
    title: str
    content: str
    section: str
    html: str


async def fetch_page(url: str, use_cache: bool = True) -> str:
    """Fetch a page's HTML, checking R2 cache first."""
    tracer = get_tracer("crawler")

    with tracer.start_as_current_span("fetch_page") as span:
        span.set_attribute("url", url[:500])
        span.set_attribute("use_cache", use_cache)

        if use_cache and settings.r2_account_id:
            cached = get_document(url)
            if cached:
                span.set_attribute("cache_hit", True)
                span.set_attribute("content_length", len(cached))
                return cached

        span.set_attribute("cache_hit", False)
        t0 = time.perf_counter()
        async with httpx.AsyncClient(
            timeout=30.0,
            follow_redirects=True,
            headers={"User-Agent": "OvidiusDocQA/0.1 (research project)"},
        ) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            html = resp.text
        fetch_ms = round((time.perf_counter() - t0) * 1000, 1)
        span.set_attribute("http_fetch_ms", fetch_ms)
        span.set_attribute("http_status", resp.status_code)
        span.set_attribute("content_length", len(html))

        if settings.r2_account_id:
            store_document(url, html, metadata={"content-length": str(len(html))})

    return html


def _table_to_markdown(table: Tag) -> str:
    """Convert an HTML <table> to a markdown table preserving column structure."""
    rows = table.find_all("tr")
    if not rows:
        return ""

    grid: list[list[str]] = []
    for row in rows:
        cells = row.find_all(["th", "td"])
        grid.append([cell.get_text(strip=True) for cell in cells])

    if not grid:
        return ""

    max_cols = max(len(r) for r in grid)
    for row in grid:
        while len(row) < max_cols:
            row.append("")

    col_widths = [
        max(len(row[c]) for row in grid)
        for c in range(max_cols)
    ]
    col_widths = [max(w, 3) for w in col_widths]

    def fmt_row(row: list[str]) -> str:
        return "| " + " | ".join(cell.ljust(w) for cell, w in zip(row, col_widths)) + " |"

    lines = [fmt_row(grid[0])]
    lines.append("| " + " | ".join("-" * w for w in col_widths) + " |")
    for row in grid[1:]:
        lines.append(fmt_row(row))

    return "\n".join(lines)


def _convert_tables_in_place(soup: BeautifulSoup) -> int:
    """Replace all <table> elements with their markdown representation."""
    tables = soup.find_all("table")
    count = 0
    for table in tables:
        md = _table_to_markdown(table)
        if md:
            table.replace_with(md)
            count += 1
        else:
            table.decompose()
    return count


def parse_page(url: str, html: str) -> RawDocument:
    """Extract clean text content from HTML, preserving table structure as markdown."""
    tracer = get_tracer("crawler")

    with tracer.start_as_current_span("parse_page") as span:
        span.set_attribute("url", url[:500])
        span.set_attribute("html_length", len(html))

        t0 = time.perf_counter()
        soup = BeautifulSoup(html, "html.parser")

        for tag in soup.find_all(["nav", "header", "footer", "script", "style", "aside"]):
            tag.decompose()

        table_count = _convert_tables_in_place(soup)
        span.set_attribute("tables_converted", table_count)

        title = soup.title.string if soup.title else urlparse(url).path
        main = soup.find("main") or soup.find("article") or soup.find(role="main") or soup.body
        content = main.get_text(separator="\n", strip=True) if main else ""
        parse_ms = round((time.perf_counter() - t0) * 1000, 1)

        parsed = urlparse(url)
        path_parts = [p for p in parsed.path.split("/") if p]
        section = path_parts[0] if path_parts else ""

        span.set_attribute("parse_ms", parse_ms)
        span.set_attribute("content_length", len(content))
        span.set_attribute("title", (title.strip() if title else url)[:200])

    return RawDocument(
        url=url,
        title=title.strip() if title else url,
        content=content,
        section=section,
        html=html,
    )


async def crawl_url(url: str, use_cache: bool = True) -> RawDocument:
    """Crawl a single URL and return parsed document."""
    tracer = get_tracer("crawler")

    with tracer.start_as_current_span("crawl_url") as span:
        span.set_attribute("url", url[:500])
        html = await fetch_page(url, use_cache=use_cache)
        doc = parse_page(url, html)
        span.set_attribute("title", doc.title[:200])
        span.set_attribute("content_length", len(doc.content))

    return doc


async def crawl_docs(base_url: str, paths: list[str], use_cache: bool = True) -> list[RawDocument]:
    """Crawl a batch of pages from a documentation site."""
    docs = []
    for path in paths:
        url = f"{base_url.rstrip('/')}/{path.lstrip('/')}"
        try:
            doc = await crawl_url(url, use_cache=use_cache)
            docs.append(doc)
        except Exception as e:
            print(f"  Failed to crawl {url}: {e}")
    return docs


async def crawl_urls(urls: list[str], use_cache: bool = True) -> list[RawDocument]:
    """Crawl a list of arbitrary URLs."""
    docs = []
    for url in urls:
        try:
            doc = await crawl_url(url, use_cache=use_cache)
            docs.append(doc)
        except Exception as e:
            print(f"  Failed to crawl {url}: {e}")
    return docs
