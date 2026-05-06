"""Agent tools — search, retrieve sections, compare sources, calculate tax.

Each tool definition follows the Anthropic tool_use schema. The handle_tool_call
dispatcher routes calls and optionally checks a ToolCache to skip redundant work.
"""

import json
import time

from app.db import get_pool
from app.retrieval.context_builder import retrieve
from app.telemetry import get_tracer


# ---------------------------------------------------------------------------
# Tool cache — avoids duplicate retrievals within a session
# ---------------------------------------------------------------------------

class ToolCache:
    def __init__(self):
        self._cache: dict[str, str] = {}

    def _key(self, tool_name: str, input_data: dict) -> str:
        return f"{tool_name}:{json.dumps(input_data, sort_keys=True)}"

    def get(self, tool_name: str, input_data: dict) -> str | None:
        return self._cache.get(self._key(tool_name, input_data))

    def set(self, tool_name: str, input_data: dict, result: str):
        self._cache[self._key(tool_name, input_data)] = result


# ---------------------------------------------------------------------------
# Tool definitions (Anthropic tool_use schema)
# ---------------------------------------------------------------------------

TOOL_DEFINITIONS = [
    {
        "name": "search_knowledge_base",
        "description": (
            "Search the IRS documentation knowledge base for passages relevant to a query. "
            "Uses hybrid search (vector similarity + BM25 keyword matching), cross-encoder "
            "reranking, and corrective RAG evaluation to find the most relevant passages. "
            "Returns source citations with confidence assessment. Use this as your primary "
            "tool for answering tax questions."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The search query — be specific. Prefer 'standard deduction for single filer 2025' over 'deductions'.",
                },
                "top_k": {
                    "type": "integer",
                    "description": "Number of results to return (1-10, default 5).",
                    "default": 5,
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "get_document_section",
        "description": (
            "Retrieve a full parent document section by its parent ID. Use this when you "
            "found a relevant passage via search and need the broader context — for example, "
            "to read the complete list of requirements, see surrounding examples, or understand "
            "the full scope of a publication section."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "parent_id": {
                    "type": "string",
                    "description": "The parent_id from a previous search result.",
                },
            },
            "required": ["parent_id"],
        },
    },
    {
        "name": "compare_sources",
        "description": (
            "Search for multiple topics and present results side by side. Use this when the "
            "user's question involves comparing two concepts (e.g., 'Roth vs traditional IRA'), "
            "when you need information from multiple publications, or when resolving an apparent "
            "conflict between sources."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "queries": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "2-4 distinct search queries, one per topic to compare.",
                    "minItems": 2,
                    "maxItems": 4,
                },
            },
            "required": ["queries"],
        },
    },
    {
        "name": "calculate_tax",
        "description": (
            "Perform simple tax calculations for the 2025 tax year. Supports standard deduction "
            "lookups, federal income tax bracket calculations, and EITC/CTC credit phase-out "
            "estimates. Use this to verify arithmetic and provide concrete numbers. Note: these "
            "are estimates — always recommend consulting a tax professional."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "calculation_type": {
                    "type": "string",
                    "enum": ["standard_deduction", "tax_bracket", "credit_phaseout"],
                    "description": "Type of calculation to perform.",
                },
                "filing_status": {
                    "type": "string",
                    "enum": ["single", "married_jointly", "married_separately", "head_of_household"],
                    "description": "Tax filing status.",
                },
                "income": {
                    "type": "number",
                    "description": "Gross income in dollars (required for tax_bracket and credit_phaseout).",
                    "default": 0,
                },
                "credit_type": {
                    "type": "string",
                    "enum": ["eitc", "ctc"],
                    "description": "Credit type (only for credit_phaseout calculation).",
                },
            },
            "required": ["calculation_type", "filing_status"],
        },
    },
]


# ---------------------------------------------------------------------------
# 2025 Tax Data (for calculate_tax tool)
# ---------------------------------------------------------------------------

STANDARD_DEDUCTIONS_2025 = {
    "single": 15_000,
    "married_jointly": 30_000,
    "married_separately": 15_000,
    "head_of_household": 22_500,
}

TAX_BRACKETS_2025 = {
    "single": [
        (11_925, 0.10),
        (48_475, 0.12),
        (103_350, 0.22),
        (197_300, 0.24),
        (250_525, 0.32),
        (626_350, 0.35),
        (float("inf"), 0.37),
    ],
    "married_jointly": [
        (23_850, 0.10),
        (96_950, 0.12),
        (206_700, 0.22),
        (394_600, 0.24),
        (501_050, 0.32),
        (751_600, 0.35),
        (float("inf"), 0.37),
    ],
    "married_separately": [
        (11_925, 0.10),
        (48_475, 0.12),
        (103_350, 0.22),
        (197_300, 0.24),
        (250_525, 0.32),
        (375_800, 0.35),
        (float("inf"), 0.37),
    ],
    "head_of_household": [
        (17_000, 0.10),
        (64_850, 0.12),
        (103_350, 0.22),
        (197_300, 0.24),
        (250_500, 0.32),
        (626_350, 0.35),
        (float("inf"), 0.37),
    ],
}

EITC_PHASEOUT_2025 = {
    "single": {"max_credit": 632, "phaseout_start": 10_620, "phaseout_end": 19_104},
    "married_jointly": {"max_credit": 632, "phaseout_start": 17_250, "phaseout_end": 25_734},
}

CTC_2025 = {
    "per_child": 2_000,
    "phaseout_start": {"single": 200_000, "married_jointly": 400_000},
    "phaseout_rate": 50,  # $50 per $1,000 over threshold
}


# ---------------------------------------------------------------------------
# Tool handlers
# ---------------------------------------------------------------------------

async def _handle_search(input_data: dict) -> str:
    retrieval_result = await retrieve(
        input_data["query"],
        top_k=input_data.get("top_k", 5),
    )

    children = retrieval_result.children
    parent_contents = retrieval_result.parent_contents
    confidence = retrieval_result.corrective.confidence.value

    results = []
    for i, c in enumerate(children):
        parent_text = parent_contents.get(c.parent_id, "")
        content = parent_text if parent_text else (c.contextual_content or c.content)
        results.append(
            f"[{i + 1}] Source: {c.source_title} ({c.source_url})\n"
            f"Parent ID: {c.parent_id} | Type: {c.document_type} | Method: {c.retrieval_method}\n"
            f"{content}"
        )

    header = f"Retrieval confidence: {confidence}\n\n"
    body = "\n\n".join(results) if results else "No relevant passages found."
    return header + body


async def _handle_get_section(input_data: dict) -> str:
    parent_id = input_data["parent_id"]

    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT content, source_url, source_title, section, document_type "
            "FROM parent_chunks WHERE parent_id = $1",
            parent_id,
        )

    if not row:
        return f"No document section found with parent_id '{parent_id}'."

    return (
        f"Source: {row['source_title']} ({row['source_url']})\n"
        f"Section: {row['section']} | Type: {row['document_type']}\n"
        f"---\n{row['content']}"
    )


async def _handle_compare(input_data: dict) -> str:
    queries = input_data["queries"]
    sections = []

    for i, query in enumerate(queries):
        retrieval_result = await retrieve(query, top_k=3)
        confidence = retrieval_result.corrective.confidence.value

        sources = []
        seen_parents = set()
        for c in retrieval_result.children:
            if c.parent_id in seen_parents:
                continue
            seen_parents.add(c.parent_id)
            parent_text = retrieval_result.parent_contents.get(c.parent_id, "")
            content = parent_text if parent_text else (c.contextual_content or c.content)
            sources.append(
                f"  - {c.source_title} ({c.source_url}): {content[:500]}"
            )

        sections.append(
            f"## Query {i + 1}: \"{query}\" (confidence: {confidence})\n"
            + "\n".join(sources)
        )

    return "\n\n".join(sections)


def _handle_calculate(input_data: dict) -> str:
    calc_type = input_data["calculation_type"]
    status = input_data["filing_status"]
    income = input_data.get("income", 0)

    if calc_type == "standard_deduction":
        amount = STANDARD_DEDUCTIONS_2025.get(status, 0)
        return (
            f"2025 Standard Deduction for {status.replace('_', ' ').title()}: ${amount:,}\n"
            f"Note: Additional amounts apply for age 65+ ($1,950 single, $1,550 married) "
            f"and blindness."
        )

    elif calc_type == "tax_bracket":
        if income <= 0:
            return "Error: income must be positive for tax bracket calculation."

        std_ded = STANDARD_DEDUCTIONS_2025.get(status, 0)
        taxable = max(0, income - std_ded)

        brackets = TAX_BRACKETS_2025.get(status, TAX_BRACKETS_2025["single"])
        tax = 0.0
        prev_limit = 0
        breakdown = []

        for limit, rate in brackets:
            if taxable <= prev_limit:
                break
            bracket_income = min(taxable, limit) - prev_limit
            bracket_tax = bracket_income * rate
            tax += bracket_tax
            if bracket_income > 0:
                breakdown.append(
                    f"  ${prev_limit:>10,} – ${min(taxable, limit):>10,} at {rate:.0%}: ${bracket_tax:,.2f}"
                )
            prev_limit = limit

        effective_rate = (tax / income * 100) if income > 0 else 0
        marginal_rate = 0
        for limit, rate in brackets:
            if taxable <= limit:
                marginal_rate = rate
                break

        lines = [
            f"2025 Federal Income Tax Estimate — {status.replace('_', ' ').title()}",
            f"Gross Income:        ${income:>12,.2f}",
            f"Standard Deduction:  ${std_ded:>12,}",
            f"Taxable Income:      ${taxable:>12,.2f}",
            "",
            "Bracket Breakdown:",
            *breakdown,
            "",
            f"Estimated Tax:       ${tax:>12,.2f}",
            f"Effective Rate:      {effective_rate:>11.1f}%",
            f"Marginal Rate:       {marginal_rate:>11.0%}",
            "",
            "⚠️  Estimate only. Does not include credits, AMT, NIIT, or state tax.",
        ]
        return "\n".join(lines)

    elif calc_type == "credit_phaseout":
        credit_type = input_data.get("credit_type", "eitc")

        if credit_type == "eitc":
            key = "married_jointly" if status == "married_jointly" else "single"
            data = EITC_PHASEOUT_2025.get(key)
            if not data:
                return f"EITC data not available for filing status: {status}"

            if income <= data["phaseout_start"]:
                credit = data["max_credit"]
            elif income >= data["phaseout_end"]:
                credit = 0
            else:
                reduction = (income - data["phaseout_start"]) / (data["phaseout_end"] - data["phaseout_start"]) * data["max_credit"]
                credit = max(0, data["max_credit"] - reduction)

            return (
                f"2025 EITC Estimate (no qualifying children) — {status.replace('_', ' ').title()}\n"
                f"Income: ${income:,.2f}\n"
                f"Max Credit: ${data['max_credit']:,}\n"
                f"Phase-out Range: ${data['phaseout_start']:,} – ${data['phaseout_end']:,}\n"
                f"Estimated Credit: ${credit:,.2f}\n"
                f"⚠️  EITC with qualifying children has higher maximums. This is the base estimate."
            )

        elif credit_type == "ctc":
            threshold = CTC_2025["phaseout_start"].get(
                "married_jointly" if status == "married_jointly" else "single", 200_000
            )
            per_child = CTC_2025["per_child"]
            if income <= threshold:
                phase_reduction = 0
            else:
                excess_thousands = (income - threshold) / 1_000
                phase_reduction = excess_thousands * CTC_2025["phaseout_rate"]

            credit_per_child = max(0, per_child - phase_reduction)

            return (
                f"2025 Child Tax Credit Phase-out — {status.replace('_', ' ').title()}\n"
                f"Income: ${income:,.2f}\n"
                f"Phase-out Threshold: ${threshold:,}\n"
                f"Credit per Child (before phase-out): ${per_child:,}\n"
                f"Phase-out Reduction per Child: ${phase_reduction:,.2f}\n"
                f"Credit per Child (after phase-out): ${credit_per_child:,.2f}\n"
                f"⚠️  Multiply by number of qualifying children under 17."
            )

        return f"Unknown credit type: {credit_type}"

    return f"Unknown calculation type: {calc_type}"


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------

_HANDLERS = {
    "search_knowledge_base": _handle_search,
    "get_document_section": _handle_get_section,
    "compare_sources": _handle_compare,
    "calculate_tax": _handle_calculate,
}


async def handle_tool_call(
    name: str,
    input_data: dict,
    cache: ToolCache | None = None,
) -> str:
    """Execute a tool call. Uses cache if provided to skip redundant work."""
    tracer = get_tracer("agent.tools")

    with tracer.start_as_current_span(f"tool:{name}") as span:
        span.set_attribute("tool.name", name)
        span.set_attribute("tool.input", json.dumps(input_data)[:500])

        if cache:
            cached = cache.get(name, input_data)
            if cached is not None:
                span.set_attribute("tool.cache_hit", True)
                return cached

        handler = _HANDLERS.get(name)
        if handler is None:
            return f"Unknown tool: {name}"

        t0 = time.perf_counter()

        import asyncio
        if asyncio.iscoroutinefunction(handler):
            result = await handler(input_data)
        else:
            result = handler(input_data)

        elapsed = round((time.perf_counter() - t0) * 1000, 1)
        span.set_attribute("tool.duration_ms", elapsed)
        span.set_attribute("tool.result_length", len(result))

        if cache:
            cache.set(name, input_data, result)

        return result
