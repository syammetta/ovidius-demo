"""Shared fixtures and mocks for all tests."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.retrieval.vector_store import RetrievedChunk
from app.ingestion.chunker import ParentChunk, ChildChunk, ChunkResult


# ---------------------------------------------------------------------------
# Sample data factories
# ---------------------------------------------------------------------------

def make_chunk(
    chunk_id: str = "c_abc123_0",
    parent_id: str = "p_def456_0",
    content: str = "The standard deduction for single filers is $15,000.",
    source_url: str = "https://www.irs.gov/publications/p501",
    source_title: str = "Publication 501 - Dependents",
    section: str = "publications",
    document_type: str = "narrative",
    score: float = 0.85,
    retrieval_method: str = "vector",
    contextual_content: str | None = None,
) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=chunk_id,
        parent_id=parent_id,
        content=content,
        contextual_content=contextual_content,
        source_url=source_url,
        source_title=source_title,
        section=section,
        document_type=document_type,
        score=score,
        retrieval_method=retrieval_method,
    )


def make_parent_chunk(
    parent_id: str = "p_def456_0",
    content: str = "## Standard Deduction\n\nThe standard deduction for single filers is $15,000. For married filing jointly, it is $30,000.",
    source_url: str = "https://www.irs.gov/publications/p501",
    source_title: str = "Publication 501",
    section: str = "publications",
    document_type: str = "narrative",
    token_count: int = 50,
) -> ParentChunk:
    return ParentChunk(
        parent_id=parent_id,
        content=content,
        source_url=source_url,
        source_title=source_title,
        section=section,
        document_type=document_type,
        token_count=token_count,
    )


def make_child_chunk(
    chunk_id: str = "c_abc123_0",
    parent_id: str = "p_def456_0",
    content: str = "The standard deduction for single filers is $15,000.",
    source_url: str = "https://www.irs.gov/publications/p501",
    source_title: str = "Publication 501",
    section: str = "publications",
    document_type: str = "narrative",
    content_hash: str = "abc123def456",
    token_count: int = 15,
) -> ChildChunk:
    return ChildChunk(
        chunk_id=chunk_id,
        parent_id=parent_id,
        content=content,
        source_url=source_url,
        source_title=source_title,
        section=section,
        document_type=document_type,
        content_hash=content_hash,
        token_count=token_count,
    )


def make_retrieved_chunks(n: int = 5) -> list[RetrievedChunk]:
    """Create n distinct retrieved chunks for testing."""
    return [
        make_chunk(
            chunk_id=f"c_chunk{i}_0",
            parent_id=f"p_parent{i}_0",
            content=f"Tax content chunk {i} with specific details about deductions.",
            score=0.95 - (i * 0.05),
        )
        for i in range(n)
    ]


# ---------------------------------------------------------------------------
# Sample HTML fixtures
# ---------------------------------------------------------------------------

SAMPLE_NARRATIVE_HTML = """
<html>
<head><title>Publication 501 - Dependents, Standard Deduction</title></head>
<body>
<nav>Skip this navigation</nav>
<main>
<h1>Publication 501</h1>
<h2>Standard Deduction</h2>
<p>Most taxpayers have a choice of either taking a standard deduction or
itemizing their deductions. The standard deduction is a dollar amount that
reduces the amount of income on which you are taxed.</p>
<p>For 2025, the standard deduction amounts are:</p>
<ul>
<li>Single or Married Filing Separately: $15,000</li>
<li>Married Filing Jointly or Qualifying Surviving Spouse: $30,000</li>
<li>Head of Household: $22,500</li>
</ul>
<h2>Dependents</h2>
<p>You can claim a dependent if they meet certain tests. A dependent is
either a qualifying child or a qualifying relative.</p>
<h3>Qualifying Child</h3>
<p>To be your qualifying child, a child must meet all of the following:</p>
<ul>
<li>The child must be your son, daughter, stepchild, or foster child.</li>
<li>The child must be under age 19, or under 24 if a full-time student.</li>
<li>The child must have lived with you for more than half the year.</li>
</ul>
</main>
<footer>IRS footer content</footer>
</body>
</html>
"""

SAMPLE_API_REFERENCE_HTML = """
<html>
<head><title>API Reference - Tax Calculations</title></head>
<body>
<main>
<h2>Endpoint: POST /api/calculate-tax</h2>
<p>Calculate federal income tax based on filing status and income.</p>
<h3>Request Body</h3>
<p>Parameters:</p>
<table>
<tr><th>Parameter</th><th>Type</th><th>Description</th></tr>
<tr><td>filing_status</td><td>string</td><td>One of: single, married_jointly</td></tr>
<tr><td>gross_income</td><td>number</td><td>Total gross income in dollars</td></tr>
</table>
<h3>Response Body</h3>
```json
{
  "tax_owed": 12500,
  "effective_rate": 0.167,
  "marginal_bracket": "22%"
}
```
<h3>Status Codes</h3>
<p>Returns HTTP 200 on success, 422 on invalid input.</p>
</main>
</body>
</html>
"""

SAMPLE_CODE_HEAVY_HTML = """
<html>
<head><title>Tax Calculator Code Examples</title></head>
<body>
<main>
<h2>Python Tax Calculator</h2>
<p>Here is a complete example of calculating federal income tax:</p>
```python
def calculate_tax(income, filing_status="single"):
    brackets = {
        "single": [(11600, 0.10), (47150, 0.12), (100525, 0.22)],
    }
    tax = 0
    prev = 0
    for limit, rate in brackets[filing_status]:
        taxable = min(income, limit) - prev
        tax += taxable * rate
        prev = limit
        if income <= limit:
            break
    return tax
```
<p>This function handles the progressive bracket calculation.</p>
```python
# Usage example
result = calculate_tax(75000, "single")
print(f"Tax owed: ${result:,.2f}")
```
<h2>JavaScript Version</h2>
```javascript
function calculateTax(income, filingStatus = "single") {
    const brackets = { single: [[11600, 0.10], [47150, 0.12]] };
    let tax = 0;
    return tax;
}
```
</main>
</body>
</html>
"""

SAMPLE_EMPTY_HTML = """
<html><head><title>Empty Page</title></head><body><main></main></body></html>
"""

SAMPLE_MALFORMED_HTML = """
<html><head><title>Broken
<body>
<p>Some content without proper closing tags
<div>Nested <span>unclosed
<p>Another paragraph
</body>
"""

SAMPLE_NAV_HEAVY_HTML = """
<html>
<head><title>IRS Topic</title></head>
<body>
<nav><ul><li>Home</li><li>Forms</li><li>About</li></ul></nav>
<header><h1>IRS.gov Header</h1><p>Search bar etc</p></header>
<main>
<h2>Tax Topic 301 - When to File</h2>
<p>The due date for filing your return is April 15.</p>
</main>
<aside><p>Related topics sidebar</p></aside>
<footer><p>IRS footer links</p></footer>
<script>console.log("tracking");</script>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# Mock helpers
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_anthropic():
    """Mock the Anthropic client for generation/contextualizer/corrective tests."""
    with patch("anthropic.Anthropic") as mock_cls:
        client = MagicMock()
        mock_cls.return_value = client

        response = MagicMock()
        response.content = [MagicMock(text="Mocked answer with [1] citation.", type="text")]
        response.stop_reason = "end_turn"
        client.messages.create.return_value = response

        yield client


@pytest.fixture
def mock_voyage():
    """Mock the Voyage AI client for embedding tests."""
    with patch("voyageai.Client") as mock_cls:
        client = MagicMock()
        mock_cls.return_value = client

        result = MagicMock()
        result.embeddings = [[0.1] * 1024]
        client.embed.return_value = result

        yield client


@pytest.fixture
def mock_db_pool():
    """Mock the asyncpg connection pool."""
    with patch("app.db.get_pool") as mock_get:
        pool = AsyncMock()
        conn = AsyncMock()
        pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
        pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
        mock_get.return_value = pool
        yield conn


@pytest.fixture
def mock_r2():
    """Mock the R2/S3 boto3 client."""
    with patch("app.storage._get_client") as mock_get:
        client = MagicMock()
        mock_get.return_value = client
        yield client


@pytest.fixture
def mock_flashrank():
    """Mock FlashRank ranker."""
    with patch("app.retrieval.reranker._get_ranker") as mock_get:
        ranker = MagicMock()
        mock_get.return_value = ranker
        yield ranker
