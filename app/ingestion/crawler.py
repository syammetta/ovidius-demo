"""Crawl documentation pages with R2 caching.

Supports three ingestion modes:
1. Batch: predefined source lists (base_url + paths)
2. Single URL: ingest any arbitrary web page
3. Cached: skip crawling, use R2-stored content

On every crawl, raw HTML is stored in R2 so subsequent runs are instant.
"""

from dataclasses import dataclass
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup

from app.config import settings
from app.storage import store_document, get_document


@dataclass
class RawDocument:
    url: str
    title: str
    content: str
    section: str
    html: str


async def fetch_page(url: str, use_cache: bool = True) -> str:
    """Fetch a page's HTML, checking R2 cache first."""
    if use_cache and settings.r2_account_id:
        cached = get_document(url)
        if cached:
            return cached

    async with httpx.AsyncClient(
        timeout=30.0,
        follow_redirects=True,
        headers={"User-Agent": "OvidiusDocQA/0.1 (research project)"},
    ) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        html = resp.text

    if settings.r2_account_id:
        store_document(url, html, metadata={"content-length": str(len(html))})

    return html


def parse_page(url: str, html: str) -> RawDocument:
    """Extract clean text content from HTML."""
    soup = BeautifulSoup(html, "html.parser")

    for tag in soup.find_all(["nav", "header", "footer", "script", "style", "aside"]):
        tag.decompose()

    title = soup.title.string if soup.title else urlparse(url).path
    main = soup.find("main") or soup.find("article") or soup.find(role="main") or soup.body
    content = main.get_text(separator="\n", strip=True) if main else ""

    parsed = urlparse(url)
    path_parts = [p for p in parsed.path.split("/") if p]
    section = path_parts[0] if path_parts else ""

    return RawDocument(
        url=url,
        title=title.strip() if title else url,
        content=content,
        section=section,
        html=html,
    )


async def crawl_url(url: str, use_cache: bool = True) -> RawDocument:
    """Crawl a single URL and return parsed document."""
    html = await fetch_page(url, use_cache=use_cache)
    return parse_page(url, html)


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
