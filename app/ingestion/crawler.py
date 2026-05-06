"""Crawl public documentation sites and extract content."""

from dataclasses import dataclass

import httpx
from bs4 import BeautifulSoup


@dataclass
class RawDocument:
    url: str
    title: str
    content: str
    section: str


async def crawl_docs(base_url: str, paths: list[str]) -> list[RawDocument]:
    """Fetch and parse documentation pages."""
    docs = []
    async with httpx.AsyncClient(timeout=30.0) as client:
        for path in paths:
            url = f"{base_url.rstrip('/')}/{path.lstrip('/')}"
            resp = await client.get(url)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "html.parser")

            title = soup.title.string if soup.title else path
            main = soup.find("main") or soup.find("article") or soup.body
            content = main.get_text(separator="\n", strip=True) if main else ""

            docs.append(RawDocument(
                url=url,
                title=title.strip(),
                content=content,
                section=path.split("/")[0] if "/" in path else "",
            ))
    return docs
