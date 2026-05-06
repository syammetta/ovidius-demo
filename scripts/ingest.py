"""Ingest documentation into the vector store.

Pipeline: crawl (with R2 caching) → adaptive chunk → contextualize → embed → store.

Usage:
  python -m scripts.ingest                    # ingest full IRS corpus
  python -m scripts.ingest --url <url>        # ingest a single URL
  python -m scripts.ingest --urls-file <file> # ingest URLs from a file (one per line)
  python -m scripts.ingest --no-cache         # skip R2 cache, force re-crawl
"""

import argparse
import asyncio

from app.ingestion.crawler import crawl_docs, crawl_url, crawl_urls
from app.ingestion.chunker import chunk_document
from app.ingestion.contextualizer import contextualize_chunks
from app.ingestion.embedder import store_parents, embed_and_store_children
from app.db import get_pool, close_pool

# IRS Tax Documentation Corpus
# Covers: individual taxes, deductions, credits, investments, retirement, small business
# Document types: narrative guides, tables/worksheets, form instructions, worked examples
IRS_PUBLICATIONS = {
    "https://www.irs.gov/publications": [
        "p17",    # Your Federal Income Tax (master guide, ~142 pages)
        "p501",   # Dependents, Standard Deduction, Filing Info
        "p502",   # Medical and Dental Expenses
        "p503",   # Child and Dependent Care Expenses
        "p505",   # Tax Withholding and Estimated Tax
        "p525",   # Taxable and Nontaxable Income
        "p526",   # Charitable Contributions
        "p527",   # Residential Rental Property
        "p530",   # Tax Information for Homeowners
        "p523",   # Selling Your Home
        "p550",   # Investment Income and Expenses
        "p590a",  # Contributions to IRAs
        "p590b",  # Distributions from IRAs
        "p596",   # Earned Income Credit
        "p936",   # Home Mortgage Interest Deduction
        "p970",   # Tax Benefits for Education
        "p969",   # Health Savings Accounts
        "p974",   # Premium Tax Credit (ACA)
        "p334",   # Tax Guide for Small Business
        "p587",   # Business Use of Your Home
    ],
}

# IRS Tax Topics — concise summaries, good for quick retrieval
IRS_TAX_TOPICS = [
    "https://www.irs.gov/taxtopics/tc301",  # When, how, and where to file
    "https://www.irs.gov/taxtopics/tc303",  # Checklist of common errors
    "https://www.irs.gov/taxtopics/tc401",  # Wages and salaries
    "https://www.irs.gov/taxtopics/tc403",  # Interest received
    "https://www.irs.gov/taxtopics/tc404",  # Dividends
    "https://www.irs.gov/taxtopics/tc409",  # Capital gains and losses
    "https://www.irs.gov/taxtopics/tc410",  # Pensions and annuities
    "https://www.irs.gov/taxtopics/tc414",  # Rental income and expenses
    "https://www.irs.gov/taxtopics/tc418",  # Unemployment compensation
    "https://www.irs.gov/taxtopics/tc419",  # Gambling income and losses
    "https://www.irs.gov/taxtopics/tc421",  # Scholarship and fellowship grants
    "https://www.irs.gov/taxtopics/tc451",  # Individual retirement arrangements
    "https://www.irs.gov/taxtopics/tc452",  # Alimony and separate maintenance
    "https://www.irs.gov/taxtopics/tc453",  # Bad debt deduction
    "https://www.irs.gov/taxtopics/tc456",  # Student loan interest deduction
    "https://www.irs.gov/taxtopics/tc501",  # Should I itemize?
    "https://www.irs.gov/taxtopics/tc502",  # Medical and dental expenses
    "https://www.irs.gov/taxtopics/tc503",  # Deductible taxes
    "https://www.irs.gov/taxtopics/tc504",  # Home mortgage points
    "https://www.irs.gov/taxtopics/tc505",  # Interest expense
    "https://www.irs.gov/taxtopics/tc506",  # Charitable contributions
    "https://www.irs.gov/taxtopics/tc509",  # Business use of home
    "https://www.irs.gov/taxtopics/tc551",  # Standard deduction
    "https://www.irs.gov/taxtopics/tc553",  # Tax on a child's investment income
    "https://www.irs.gov/taxtopics/tc554",  # Self-employment tax
    "https://www.irs.gov/taxtopics/tc556",  # Alternative minimum tax
    "https://www.irs.gov/taxtopics/tc601",  # Earned income credit
    "https://www.irs.gov/taxtopics/tc602",  # Child and dependent care credit
    "https://www.irs.gov/taxtopics/tc607",  # Adoption credit and exclusion
    "https://www.irs.gov/taxtopics/tc610",  # Retirement savings contributions credit
    "https://www.irs.gov/taxtopics/tc611",  # Repayment of the first-time homebuyer credit
    "https://www.irs.gov/taxtopics/tc612",  # Premium tax credit
]

# Key form instructions
IRS_INSTRUCTIONS = {
    "https://www.irs.gov/instructions": [
        "i1040gi",  # Form 1040 general instructions
        "i1040sa",  # Schedule A (Itemized Deductions)
        "i1040sc",  # Schedule C (Profit or Loss from Business)
        "i1040sd",  # Schedule D (Capital Gains and Losses)
        "i1040se",  # Schedule E (Rental, Royalty, Partnership)
    ],
}


async def ingest_document(doc, stats: dict):
    """Process a single document through the full pipeline."""
    print(f"\n  Processing: {doc.title[:80]}")

    result = chunk_document(doc.content, doc.url, doc.title, doc.section)
    doc_type = result.parents[0].document_type if result.parents else "unknown"
    print(f"    Type: {doc_type} | Parents: {len(result.parents)} | Children: {len(result.children)}")

    if not result.children:
        print("    Skipping — no chunks produced")
        return

    print(f"    Contextualizing {len(result.children)} chunks...")
    contextualized = await contextualize_chunks(result.children, result.parents)

    print("    Storing parents...")
    await store_parents(result.parents)

    print("    Embedding and storing children...")
    await embed_and_store_children(contextualized)

    stats["parents"] += len(result.parents)
    stats["children"] += len(contextualized)
    stats["pages"] += 1


async def ingest_corpus(use_cache: bool = True):
    """Ingest the full IRS documentation corpus."""
    stats = {"pages": 0, "parents": 0, "children": 0}

    # Publications
    for base_url, paths in IRS_PUBLICATIONS.items():
        print(f"\nCrawling IRS Publications from {base_url}...")
        docs = await crawl_docs(base_url, paths, use_cache=use_cache)
        print(f"  Fetched {len(docs)} publications")
        for doc in docs:
            await ingest_document(doc, stats)

    # Tax Topics
    print(f"\nCrawling {len(IRS_TAX_TOPICS)} IRS Tax Topics...")
    docs = await crawl_urls(IRS_TAX_TOPICS, use_cache=use_cache)
    print(f"  Fetched {len(docs)} tax topics")
    for doc in docs:
        await ingest_document(doc, stats)

    # Form Instructions
    for base_url, paths in IRS_INSTRUCTIONS.items():
        print(f"\nCrawling IRS Form Instructions from {base_url}...")
        docs = await crawl_docs(base_url, paths, use_cache=use_cache)
        print(f"  Fetched {len(docs)} instruction sets")
        for doc in docs:
            await ingest_document(doc, stats)

    return stats


async def ingest_single_url(url: str, use_cache: bool = True):
    """Ingest a single URL."""
    stats = {"pages": 0, "parents": 0, "children": 0}
    doc = await crawl_url(url, use_cache=use_cache)
    await ingest_document(doc, stats)
    return stats


async def ingest_urls_file(file_path: str, use_cache: bool = True):
    """Ingest URLs from a file (one per line)."""
    with open(file_path) as f:
        urls = [line.strip() for line in f if line.strip() and not line.startswith("#")]

    stats = {"pages": 0, "parents": 0, "children": 0}
    docs = await crawl_urls(urls, use_cache=use_cache)
    for doc in docs:
        await ingest_document(doc, stats)
    return stats


async def main():
    parser = argparse.ArgumentParser(description="Ingest documents into the knowledge base")
    parser.add_argument("--url", help="Ingest a single URL")
    parser.add_argument("--urls-file", help="Ingest URLs from a file (one per line)")
    parser.add_argument("--no-cache", action="store_true", help="Skip R2 cache, force re-crawl")
    args = parser.parse_args()

    use_cache = not args.no_cache
    await get_pool()

    try:
        if args.url:
            print(f"Ingesting single URL: {args.url}")
            stats = await ingest_single_url(args.url, use_cache=use_cache)
        elif args.urls_file:
            print(f"Ingesting URLs from: {args.urls_file}")
            stats = await ingest_urls_file(args.urls_file, use_cache=use_cache)
        else:
            print("Ingesting full IRS documentation corpus...")
            stats = await ingest_corpus(use_cache=use_cache)

        print("\nDone.")
        print(f"  Pages processed:  {stats['pages']}")
        print(f"  Parent chunks:    {stats['parents']}")
        print(f"  Child chunks:     {stats['children']}")

    finally:
        await close_pool()


if __name__ == "__main__":
    asyncio.run(main())
