"""Query classifier — determines intent, topics, and retrieval strategy.

Classifies incoming questions so the pipeline can adapt:
- Factual lookups get tight, metadata-filtered retrieval
- Comparisons get wider search across multiple topics
- Calculations route through deterministic tools + verification
- Procedural questions prioritize form instructions
- Complex multi-hop questions use broad search or agent escalation

This is the "no one size fits all" routing layer.
"""

import json
import logging
import time
from dataclasses import dataclass, field

import anthropic

from app.config import settings
from app.telemetry import get_tracer

logger = logging.getLogger(__name__)

INTENT_TYPES = ["factual", "comparison", "calculation", "procedural", "complex"]

TOPIC_CATEGORIES = [
    "income", "deductions", "credits", "retirement", "filing",
    "home", "education", "medical", "business", "investment",
    "family", "employment", "self_employment", "capital_gains",
    "estate_gift", "international", "compliance", "penalties",
]

CLASSIFY_PROMPT = """Classify this tax question for retrieval routing. Return JSON only.

{{
  "intent": one of {intents},
  "topics": list from {topics} (1-3 most relevant),
  "doc_types": list from ["narrative", "api_reference", "code_heavy"] to prioritize,
  "sections": list from ["publications", "taxtopics", "instructions"] to prioritize,
  "reasoning": one sentence why
}}

Intent guide:
- factual: single fact lookup ("What is the standard deduction?")
- comparison: comparing options ("Roth vs Traditional IRA")
- calculation: needs numbers/math ("How much tax on $80k income?")
- procedural: step-by-step how-to ("How do I file Schedule C?")
- complex: multi-part, requires combining rules from multiple sources

Question: {question}

Return ONLY valid JSON."""


@dataclass
class QueryClassification:
    intent: str
    topics: list[str]
    doc_types: list[str]
    sections: list[str]
    reasoning: str = ""


@dataclass
class RetrievalStrategy:
    name: str
    top_n: int
    top_k: int
    metadata_boost: bool
    boost_doc_types: list[str] = field(default_factory=list)
    boost_sections: list[str] = field(default_factory=list)

    @property
    def description(self) -> str:
        parts = [self.name]
        if self.metadata_boost and self.boost_doc_types:
            parts.append(f"boost:{','.join(self.boost_doc_types)}")
        parts.append(f"top_n={self.top_n}")
        parts.append(f"top_k={self.top_k}")
        return " | ".join(parts)


STRATEGY_MAP: dict[str, dict] = {
    "factual": {"top_n": 15, "top_k": 4, "name": "Focused Lookup"},
    "comparison": {"top_n": 25, "top_k": 8, "name": "Multi-Topic Scan"},
    "calculation": {"top_n": 15, "top_k": 4, "name": "Reference Lookup"},
    "procedural": {"top_n": 20, "top_k": 5, "name": "Step-by-Step"},
    "complex": {"top_n": 25, "top_k": 8, "name": "Deep Search"},
}


async def classify_query(question: str) -> tuple[QueryClassification, RetrievalStrategy]:
    """Classify a query and determine the retrieval strategy."""
    tracer = get_tracer("classifier")
    try:
        with tracer.start_as_current_span("classify_llm_call") as span:
            span.set_attribute("model", settings.classification_model)
            client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
            t0 = time.perf_counter()
            response = await client.messages.create(
                model=settings.classification_model,
                max_tokens=200,
                messages=[{
                    "role": "user",
                    "content": CLASSIFY_PROMPT.format(
                        question=question,
                        intents=INTENT_TYPES,
                        topics=TOPIC_CATEGORIES,
                    ),
                }],
            )
            llm_ms = round((time.perf_counter() - t0) * 1000, 1)
            span.set_attribute("llm_latency_ms", llm_ms)
            span.set_attribute("input_tokens", response.usage.input_tokens)
            span.set_attribute("output_tokens", response.usage.output_tokens)

        raw = response.content[0].text.strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1].rsplit("```", 1)[0]

        data = json.loads(raw)

        classification = QueryClassification(
            intent=data.get("intent", "complex") if data.get("intent") in INTENT_TYPES else "complex",
            topics=[t for t in data.get("topics", []) if t in TOPIC_CATEGORIES][:3],
            doc_types=[d for d in data.get("doc_types", []) if d in ("narrative", "api_reference", "code_heavy")][:2],
            sections=[s for s in data.get("sections", []) if s in ("publications", "taxtopics", "instructions")][:2],
            reasoning=data.get("reasoning", ""),
        )
    except Exception as e:
        logger.warning("Query classification failed: %s", e)
        classification = QueryClassification(
            intent="complex",
            topics=[],
            doc_types=[],
            sections=[],
            reasoning="classification failed, using broad search",
        )

    base = STRATEGY_MAP.get(classification.intent, STRATEGY_MAP["complex"])
    strategy = RetrievalStrategy(
        name=base["name"],
        top_n=base["top_n"],
        top_k=base["top_k"],
        metadata_boost=bool(classification.doc_types),
        boost_doc_types=classification.doc_types,
        boost_sections=classification.sections,
    )

    return classification, strategy
