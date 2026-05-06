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

# ---------------------------------------------------------------------------
# IRS Tax Documentation Corpus
# ---------------------------------------------------------------------------
# Covers: individual taxes, deductions, credits, investments, retirement,
#         small business, self-employment, estate/gift, international,
#         compliance, penalties, and all major form instructions.
#
# Document types:
#   - Publications: comprehensive narrative guides
#   - Tax Topics: concise summaries, good for quick retrieval
#   - Instructions: step-by-step form completion guides
# ---------------------------------------------------------------------------

IRS_PUBLICATIONS = {
    "https://www.irs.gov/publications": [
        # ---- Core individual tax ----
        "p17",    # Your Federal Income Tax (master guide)
        "p501",   # Dependents, Standard Deduction, Filing Info
        "p505",   # Tax Withholding and Estimated Tax
        "p525",   # Taxable and Nontaxable Income
        "p15",    # Employer's Tax Guide (Circular E)
        "p15a",   # Employer's Supplemental Tax Guide

        # ---- Medical & health ----
        "p502",   # Medical and Dental Expenses
        "p969",   # Health Savings Accounts (HSA)
        "p974",   # Premium Tax Credit (ACA)
        "p502",   # Medical and Dental Expenses

        # ---- Family & dependents ----
        "p503",   # Child and Dependent Care Expenses
        "p504",   # Divorced or Separated Individuals
        "p929",   # Tax Rules for Children and Dependents
        "p972",   # Child Tax Credit

        # ---- Home ----
        "p523",   # Selling Your Home
        "p527",   # Residential Rental Property
        "p530",   # Tax Information for Homeowners
        "p936",   # Home Mortgage Interest Deduction
        "p587",   # Business Use of Your Home

        # ---- Education ----
        "p970",   # Tax Benefits for Education

        # ---- Charitable ----
        "p526",   # Charitable Contributions
        "p561",   # Determining the Value of Donated Property
        "p1771",  # Charitable Contributions — Substantiation

        # ---- Investment & capital gains ----
        "p550",   # Investment Income and Expenses
        "p544",   # Sales and Other Dispositions of Assets
        "p551",   # Basis of Assets
        "p564",   # Mutual Fund Distributions

        # ---- Retirement ----
        "p590a",  # Contributions to IRAs
        "p590b",  # Distributions from IRAs
        "p575",   # Pension and Annuity Income
        "p560",   # Retirement Plans for Small Business
        "p571",   # Tax-Sheltered Annuity Plans (403b)
        "p721",   # Tax Guide to U.S. Civil Service Retirement

        # ---- Credits ----
        "p596",   # Earned Income Credit (EIC)
        "p972",   # Child Tax Credit

        # ---- Small business & self-employment ----
        "p334",   # Tax Guide for Small Business
        "p535",   # Business Expenses
        "p463",   # Travel, Gift, and Car Expenses
        "p946",   # How to Depreciate Property
        "p541",   # Partnerships
        "p542",   # Corporations
        "p589",   # Tax Information on S Corporations
        "p15b",   # Employer's Tax Guide to Fringe Benefits
        "p583",   # Starting a Business and Keeping Records
        "p463",   # Travel, Gift, and Car Expenses
        "p535",   # Business Expenses
        "p536",   # Net Operating Losses (NOLs)
        "p538",   # Accounting Periods and Methods
        "p334",   # Tax Guide for Small Business

        # ---- Farm ----
        "p225",   # Farmer's Tax Guide

        # ---- Estate, gift, trusts ----
        "p559",   # Survivors, Executors, and Administrators
        "p950",   # Introduction to Estate and Gift Taxes

        # ---- International ----
        "p54",    # Tax Guide for U.S. Citizens Abroad
        "p519",   # U.S. Tax Guide for Aliens
        "p514",   # Foreign Tax Credit for Individuals
        "p901",   # U.S. Tax Treaties

        # ---- Compliance & penalties ----
        "p556",   # Examination of Returns, Appeal Rights, and Claims for Refund
        "p594",   # The IRS Collection Process
        "p1",     # Your Rights as a Taxpayer
        "p5",     # Your Appeal Rights

        # ---- Military & special ----
        "p3",     # Armed Forces' Tax Guide

        # ---- Miscellaneous ----
        "p915",   # Social Security and Equivalent Railroad Retirement Benefits
        "p524",   # Credit for the Elderly or the Disabled
        "p547",   # Casualties, Disasters, and Thefts
        "p529",   # Miscellaneous Deductions
        "p517",   # Social Security for Members of the Clergy
    ],
}

# IRS Tax Topics — concise summaries, good for quick retrieval
IRS_TAX_TOPICS = [
    # ---- Filing requirements ----
    "https://www.irs.gov/taxtopics/tc301",  # When, how, and where to file
    "https://www.irs.gov/taxtopics/tc303",  # Checklist of common errors
    "https://www.irs.gov/taxtopics/tc304",  # Extensions of time to file
    "https://www.irs.gov/taxtopics/tc305",  # Recordkeeping
    "https://www.irs.gov/taxtopics/tc306",  # Penalty for underpayment of estimated tax
    "https://www.irs.gov/taxtopics/tc308",  # Amended returns
    "https://www.irs.gov/taxtopics/tc309",  # Roth IRA contributions
    "https://www.irs.gov/taxtopics/tc310",  # Tax credits for education

    # ---- Income types ----
    "https://www.irs.gov/taxtopics/tc401",  # Wages and salaries
    "https://www.irs.gov/taxtopics/tc403",  # Interest received
    "https://www.irs.gov/taxtopics/tc404",  # Dividends
    "https://www.irs.gov/taxtopics/tc407",  # Business income
    "https://www.irs.gov/taxtopics/tc409",  # Capital gains and losses
    "https://www.irs.gov/taxtopics/tc410",  # Pensions and annuities
    "https://www.irs.gov/taxtopics/tc411",  # Pensions – general rule / simplified method
    "https://www.irs.gov/taxtopics/tc412",  # Lump-sum distributions
    "https://www.irs.gov/taxtopics/tc414",  # Rental income and expenses
    "https://www.irs.gov/taxtopics/tc415",  # Renting residential and vacation property
    "https://www.irs.gov/taxtopics/tc418",  # Unemployment compensation
    "https://www.irs.gov/taxtopics/tc419",  # Gambling income and losses
    "https://www.irs.gov/taxtopics/tc420",  # Bartering income
    "https://www.irs.gov/taxtopics/tc421",  # Scholarship and fellowship grants
    "https://www.irs.gov/taxtopics/tc425",  # Passive activities – losses and credits
    "https://www.irs.gov/taxtopics/tc427",  # Stock options
    "https://www.irs.gov/taxtopics/tc429",  # Traders in securities
    "https://www.irs.gov/taxtopics/tc431",  # Canceled debt – is it taxable?

    # ---- Retirement / IRA ----
    "https://www.irs.gov/taxtopics/tc451",  # Individual retirement arrangements
    "https://www.irs.gov/taxtopics/tc452",  # Alimony and separate maintenance
    "https://www.irs.gov/taxtopics/tc453",  # Bad debt deduction
    "https://www.irs.gov/taxtopics/tc455",  # Moving expenses (military)
    "https://www.irs.gov/taxtopics/tc456",  # Student loan interest deduction

    # ---- Deductions ----
    "https://www.irs.gov/taxtopics/tc501",  # Should I itemize?
    "https://www.irs.gov/taxtopics/tc502",  # Medical and dental expenses
    "https://www.irs.gov/taxtopics/tc503",  # Deductible taxes
    "https://www.irs.gov/taxtopics/tc504",  # Home mortgage points
    "https://www.irs.gov/taxtopics/tc505",  # Interest expense
    "https://www.irs.gov/taxtopics/tc506",  # Charitable contributions
    "https://www.irs.gov/taxtopics/tc508",  # Tax-related identity theft
    "https://www.irs.gov/taxtopics/tc509",  # Business use of home
    "https://www.irs.gov/taxtopics/tc510",  # Business use of car
    "https://www.irs.gov/taxtopics/tc511",  # Business travel expenses
    "https://www.irs.gov/taxtopics/tc513",  # Tax credits for education (deduction side)
    "https://www.irs.gov/taxtopics/tc515",  # Casualty, disaster, and theft losses

    # ---- Standard deduction & other ----
    "https://www.irs.gov/taxtopics/tc551",  # Standard deduction
    "https://www.irs.gov/taxtopics/tc553",  # Tax on a child's investment income
    "https://www.irs.gov/taxtopics/tc554",  # Self-employment tax
    "https://www.irs.gov/taxtopics/tc556",  # Alternative minimum tax

    # ---- Credits ----
    "https://www.irs.gov/taxtopics/tc601",  # Earned income credit
    "https://www.irs.gov/taxtopics/tc602",  # Child and dependent care credit
    "https://www.irs.gov/taxtopics/tc607",  # Adoption credit and exclusion
    "https://www.irs.gov/taxtopics/tc608",  # Excess Social Security tax withheld
    "https://www.irs.gov/taxtopics/tc610",  # Retirement savings contributions credit
    "https://www.irs.gov/taxtopics/tc611",  # Repayment of the first-time homebuyer credit
    "https://www.irs.gov/taxtopics/tc612",  # Premium tax credit

    # ---- IRS procedures ----
    "https://www.irs.gov/taxtopics/tc651",  # Notices – what to do
    "https://www.irs.gov/taxtopics/tc653",  # IRS notices and letters
    "https://www.irs.gov/taxtopics/tc654",  # Understanding your CP75 notice

    # ---- Payments & penalties ----
    "https://www.irs.gov/taxtopics/tc201",  # The collection process
    "https://www.irs.gov/taxtopics/tc202",  # Tax payment options
    "https://www.irs.gov/taxtopics/tc203",  # Reduced refund
    "https://www.irs.gov/taxtopics/tc204",  # Offers in compromise
    "https://www.irs.gov/taxtopics/tc205",  # Innocent spouse relief
    "https://www.irs.gov/taxtopics/tc206",  # Dishonored payments
]

# Form instructions — step-by-step guides
IRS_INSTRUCTIONS = {
    "https://www.irs.gov/instructions": [
        "i1040gi",  # Form 1040 general instructions
        "i1040sa",  # Schedule A (Itemized Deductions)
        "i1040sb",  # Schedule B (Interest and Dividends)
        "i1040sc",  # Schedule C (Profit or Loss from Business)
        "i1040sd",  # Schedule D (Capital Gains and Losses)
        "i1040se",  # Schedule E (Rental, Royalty, Partnership)
        "i1040sse", # Schedule SE (Self-Employment Tax)
        "i8812",    # Schedule 8812 (Credits for Qualifying Children)
        "i8863",    # Form 8863 (Education Credits)
        "i8880",    # Form 8880 (Retirement Savings Contributions Credit)
        "i8889",    # Form 8889 (HSA)
        "i8962",    # Form 8962 (Premium Tax Credit)
        "i1040sr",  # Form 1040-SR (for Seniors)
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
        unique_paths = list(dict.fromkeys(paths))
        print(f"\nCrawling {len(unique_paths)} IRS Publications from {base_url}...")
        docs = await crawl_docs(base_url, unique_paths, use_cache=use_cache)
        print(f"  Fetched {len(docs)} publications")
        for doc in docs:
            await ingest_document(doc, stats)

    # Tax Topics
    unique_topics = list(dict.fromkeys(IRS_TAX_TOPICS))
    print(f"\nCrawling {len(unique_topics)} IRS Tax Topics...")
    docs = await crawl_urls(unique_topics, use_cache=use_cache)
    print(f"  Fetched {len(docs)} tax topics")
    for doc in docs:
        await ingest_document(doc, stats)

    # Form Instructions
    for base_url, paths in IRS_INSTRUCTIONS.items():
        unique_paths = list(dict.fromkeys(paths))
        print(f"\nCrawling {len(unique_paths)} IRS Form Instructions from {base_url}...")
        docs = await crawl_docs(base_url, unique_paths, use_cache=use_cache)
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
