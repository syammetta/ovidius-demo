"""Evaluation runner using RAGAS metrics + custom pipeline observability.

Metrics:
- Faithfulness (RAGAS): are claims in the answer supported by context?
- Answer Relevancy (RAGAS): is the answer pertinent to the question?
- Context Precision (RAGAS): are relevant chunks ranked higher?
- Context Recall (RAGAS): did retrieval find all needed information?
- Retrieval Confidence: corrective RAG's self-assessment
- Pipeline Latency: end-to-end timing breakdown

Each eval pair includes expected source URLs for recall measurement
and difficulty tier for stratified analysis. Results are persisted to
both a JSON file and the eval_runs/eval_results DB tables.
"""

import json
import time
from pathlib import Path

from ragas import evaluate
from ragas.metrics import faithfulness, answer_relevancy, context_precision, context_recall
from ragas.dataset_schema import SingleTurnSample, EvaluationDataset

from app.config import settings
from app.retrieval.context_builder import retrieve
from app.generation.answerer import generate_answer
from app.telemetry import get_tracer, get_current_trace_id

DATASET_PATH = Path("eval/dataset.json")
RESULTS_PATH = Path("eval/results.json")


async def evaluate_pair(pair: dict) -> dict:
    """Evaluate a single QA pair through the full pipeline."""
    tracer = get_tracer("eval")
    question = pair["question"]
    expected_urls = set(pair.get("expected_source_urls", []))
    ground_truth = pair.get("expected_answer", "")

    with tracer.start_as_current_span("eval_pair") as span:
        trace_id = get_current_trace_id()
        span.set_attribute("pair_id", pair["id"])
        span.set_attribute("tier", pair.get("tier", "unknown"))
        span.set_attribute("question", question[:500])

        start = time.perf_counter()
        retrieval_result = await retrieve(question)
        retrieval_ms = (time.perf_counter() - start) * 1000

        gen_start = time.perf_counter()
        answer_result = await generate_answer(question, retrieval_result)
        generation_ms = (time.perf_counter() - gen_start) * 1000

        total_ms = (time.perf_counter() - start) * 1000

        retrieved_urls = {c.source_url for c in retrieval_result.children}
        hits = expected_urls & retrieved_urls if expected_urls else set()
        recall_at_k = len(hits) / len(expected_urls) if expected_urls else None

        contexts = [c.contextual_content or c.content for c in retrieval_result.children]

        span.set_attribute("recall_at_k", recall_at_k or -1)
        span.set_attribute("confidence", answer_result.confidence)
        span.set_attribute("total_ms", round(total_ms, 1))

    return {
        "id": pair["id"],
        "tier": pair.get("tier", "unknown"),
        "question": question,
        "answer": answer_result.answer,
        "contexts": contexts,
        "ground_truth": ground_truth,
        "retrieval_ms": round(retrieval_ms, 1),
        "generation_ms": round(generation_ms, 1),
        "total_ms": round(total_ms, 1),
        "recall_at_k": recall_at_k,
        "retrieved_urls": list(retrieved_urls),
        "confidence": answer_result.confidence,
        "retrieval_method": answer_result.retrieval_method,
        "chunks_used": answer_result.chunks_used,
        "parent_chunks_used": answer_result.parent_chunks_used,
        "trace_id": trace_id,
    }


def run_ragas_evaluation(results: list[dict]) -> dict:
    """Run RAGAS metrics across all evaluated pairs."""
    samples = []
    for r in results:
        samples.append(SingleTurnSample(
            user_input=r["question"],
            response=r["answer"],
            retrieved_contexts=r["contexts"],
            reference=r["ground_truth"] if r["ground_truth"] else r["answer"],
        ))

    dataset = EvaluationDataset(samples=samples)

    ragas_results = evaluate(
        dataset=dataset,
        metrics=[faithfulness, answer_relevancy, context_precision, context_recall],
    )

    return ragas_results.to_pandas().to_dict(orient="records")


def _compute_summary(pipeline_results: list[dict]) -> dict:
    """Compute aggregate metrics from individual pair results."""
    recall_scores = [r["recall_at_k"] for r in pipeline_results if r["recall_at_k"] is not None]
    confidence_counts = {}
    for r in pipeline_results:
        confidence_counts[r["confidence"]] = confidence_counts.get(r["confidence"], 0) + 1

    tier_breakdown = {}
    for r in pipeline_results:
        tier = r["tier"]
        if tier not in tier_breakdown:
            tier_breakdown[tier] = {"count": 0, "avg_total_ms": 0, "recalls": []}
        tier_breakdown[tier]["count"] += 1
        tier_breakdown[tier]["avg_total_ms"] += r["total_ms"]
        if r["recall_at_k"] is not None:
            tier_breakdown[tier]["recalls"].append(r["recall_at_k"])

    for tier in tier_breakdown:
        t = tier_breakdown[tier]
        t["avg_total_ms"] = round(t["avg_total_ms"] / t["count"], 1)
        t["avg_recall"] = round(sum(t["recalls"]) / len(t["recalls"]), 3) if t["recalls"] else None
        del t["recalls"]

    ragas_avg = {}
    ragas_keys = ["faithfulness", "answer_relevancy", "context_precision", "context_recall"]
    for key in ragas_keys:
        vals = [r["ragas"].get(key) for r in pipeline_results if "ragas" in r and r["ragas"].get(key) is not None]
        ragas_avg[f"avg_{key}"] = round(sum(vals) / len(vals), 3) if vals else None

    return {
        "total_pairs": len(pipeline_results),
        "avg_recall_at_k": round(sum(recall_scores) / len(recall_scores), 3) if recall_scores else None,
        "avg_total_ms": round(sum(r["total_ms"] for r in pipeline_results) / len(pipeline_results), 1),
        "confidence_distribution": confidence_counts,
        "tier_breakdown": tier_breakdown,
        **ragas_avg,
    }


async def run_eval():
    """Run full evaluation suite, write results to JSON file."""
    dataset = json.loads(DATASET_PATH.read_text())
    pairs = dataset["pairs"]

    active_pairs = [p for p in pairs if not p["question"].startswith("placeholder")]
    if not active_pairs:
        print("No evaluation pairs configured yet. Update eval/dataset.json first.")
        return

    pipeline_results = []
    for pair in active_pairs:
        print(f"Evaluating: {pair['id']}...")
        result = await evaluate_pair(pair)
        pipeline_results.append(result)

    print("\nRunning RAGAS evaluation...")
    try:
        ragas_scores = run_ragas_evaluation(pipeline_results)
        for i, scores in enumerate(ragas_scores):
            pipeline_results[i]["ragas"] = scores
    except Exception as e:
        print(f"  RAGAS evaluation failed: {e}")
        print("  Falling back to pipeline metrics only.")

    summary = _compute_summary(pipeline_results)
    summary["results"] = pipeline_results

    RESULTS_PATH.write_text(json.dumps(summary, indent=2, default=str))

    print(f"\nResults ({len(pipeline_results)} pairs):")
    if summary["avg_recall_at_k"]:
        print(f"  Avg Recall@K:     {summary['avg_recall_at_k']}")
    print(f"  Avg Latency:      {summary['avg_total_ms']}ms")
    print(f"  Confidence dist:  {summary['confidence_distribution']}")
    print(f"  Tier breakdown:   {json.dumps(summary['tier_breakdown'], indent=2)}")
    print(f"\nFull results: {RESULTS_PATH}")


async def run_eval_persisted(run_id: str):
    """Run eval and persist results to the database. Called by the /eval/run endpoint."""
    from app.db import get_pool

    pool = await get_pool()

    config_snapshot = {
        "generation_model": settings.generation_model,
        "embedding_model": settings.embedding_model,
        "rerank_top_k": settings.rerank_top_k,
        "retrieval_top_n": settings.retrieval_top_n,
    }

    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO eval_runs (run_id, config, status) VALUES ($1, $2, 'running')",
            run_id, json.dumps(config_snapshot),
        )

    dataset = json.loads(DATASET_PATH.read_text())
    pairs = dataset["pairs"]
    active_pairs = [p for p in pairs if not p["question"].startswith("placeholder")]

    pipeline_results = []
    try:
        for pair in active_pairs:
            result = await evaluate_pair(pair)
            pipeline_results.append(result)

            async with pool.acquire() as conn:
                await conn.execute(
                    """INSERT INTO eval_results
                       (run_id, pair_id, tier, question, expected_answer,
                        actual_answer, contexts, metrics, trace_id)
                       VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)""",
                    run_id,
                    result["id"],
                    result["tier"],
                    result["question"],
                    result.get("ground_truth", ""),
                    result["answer"],
                    json.dumps(result["contexts"]),
                    json.dumps({
                        "recall_at_k": result["recall_at_k"],
                        "retrieval_ms": result["retrieval_ms"],
                        "generation_ms": result["generation_ms"],
                        "total_ms": result["total_ms"],
                        "confidence": result["confidence"],
                    }),
                    result.get("trace_id"),
                )

        # Run RAGAS
        try:
            ragas_scores = run_ragas_evaluation(pipeline_results)
            for i, scores in enumerate(ragas_scores):
                pipeline_results[i]["ragas"] = scores

            # Update eval_results with RAGAS scores
            for result in pipeline_results:
                if "ragas" in result:
                    async with pool.acquire() as conn:
                        await conn.execute(
                            """UPDATE eval_results
                               SET metrics = metrics || $1::jsonb
                               WHERE run_id = $2 AND pair_id = $3""",
                            json.dumps(result["ragas"]),
                            run_id,
                            result["id"],
                        )
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning("RAGAS eval failed: %s", e)

        summary = _compute_summary(pipeline_results)

        async with pool.acquire() as conn:
            await conn.execute(
                """UPDATE eval_runs
                   SET finished_at = now(),
                       metrics = $1,
                       pair_count = $2,
                       status = 'completed'
                   WHERE run_id = $3""",
                json.dumps(summary, default=str),
                len(pipeline_results),
                run_id,
            )

        # Also write JSON for backward compat
        summary["results"] = pipeline_results
        RESULTS_PATH.write_text(json.dumps(summary, indent=2, default=str))

    except Exception as e:
        async with pool.acquire() as conn:
            await conn.execute(
                """UPDATE eval_runs
                   SET finished_at = now(), status = 'failed',
                       metrics = $1
                   WHERE run_id = $2""",
                json.dumps({"error": str(e)}),
                run_id,
            )
        raise


if __name__ == "__main__":
    import asyncio
    asyncio.run(run_eval())
