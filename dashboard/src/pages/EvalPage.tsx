import { useCallback, useEffect, useState } from "react";
import {
  triggerEvalRun,
  fetchEvalRuns,
  fetchEvalRun,
  fetchEvalSummary,
  deleteEvalRun,
} from "../api";
import type { EvalRun, EvalResult, EvalSummary } from "../api";

export default function EvalPage() {
  const [runs, setRuns] = useState<EvalRun[]>([]);
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);
  const [results, setResults] = useState<EvalResult[]>([]);
  const [summary, setSummary] = useState<EvalSummary | null>(null);
  const [triggering, setTriggering] = useState(false);
  const [loadingResults, setLoadingResults] = useState(false);
  const [error, setError] = useState("");

  const loadRuns = useCallback(async () => {
    try {
      const data = await fetchEvalRuns();
      setRuns(data);
    } catch {
      setError("Failed to load eval runs");
    }
  }, []);

  const loadSummary = useCallback(async () => {
    try {
      const data = await fetchEvalSummary();
      if (data) setSummary(data);
    } catch {
      // summary is supplemental — don't block on failure
    }
  }, []);

  useEffect(() => {
    loadRuns();
    loadSummary();
  }, [loadRuns, loadSummary]);

  async function handleTrigger() {
    setTriggering(true);
    setError("");
    try {
      const { run_id } = await triggerEvalRun();
      setSelectedRunId(run_id);
      await loadRuns();
    } catch {
      setError("Failed to start evaluation run");
    }
    setTriggering(false);
  }

  async function handleDelete(e: React.MouseEvent, runId: string) {
    e.stopPropagation();
    if (!window.confirm("Delete this evaluation run and all its results?")) return;
    setError("");
    try {
      await deleteEvalRun(runId);
      setRuns((prev) => prev.filter((r) => r.run_id !== runId));
      if (selectedRunId === runId) {
        setSelectedRunId(null);
        setResults([]);
      }
      await loadSummary();
    } catch {
      setError("Failed to delete eval run");
    }
  }

  async function handleSelectRun(runId: string) {
    if (selectedRunId === runId) {
      setSelectedRunId(null);
      setResults([]);
      return;
    }
    setSelectedRunId(runId);
    setLoadingResults(true);
    setError("");
    try {
      const data = await fetchEvalRun(runId);
      if (data) setResults(data.results);
    } catch {
      setError("Failed to load eval results");
    }
    setLoadingResults(false);
  }

  const scoreColor = (score: number | null | undefined) => {
    if (score == null) return "text-[var(--text-muted)]";
    if (score >= 0.8) return "text-[var(--green)]";
    if (score >= 0.5) return "text-[var(--yellow)]";
    return "text-[var(--red)]";
  };

  const scoreBg = (score: number | null | undefined) => {
    if (score == null) return "bg-[var(--bg-tertiary)]";
    if (score >= 0.8) return "bg-[var(--green-light)]";
    if (score >= 0.5) return "bg-[var(--yellow-light)]";
    return "bg-[var(--red-light)]";
  };

  const statusBadge = (status: string) =>
    status === "completed"
      ? "bg-[var(--green-light)] text-[var(--green)]"
      : status === "failed"
        ? "bg-[var(--red-light)] text-[var(--red)]"
        : "bg-[var(--accent-light)] text-[var(--accent)]";

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="px-6 py-4 border-b border-[var(--border-light)] flex items-center justify-between">
        <div>
          <h1 className="text-lg font-medium text-[var(--text)]">Evaluations</h1>
          <p className="text-sm text-[var(--text-muted)]">Run and review RAG evaluation metrics</p>
        </div>
        <button
          onClick={handleTrigger}
          disabled={triggering}
          className="px-5 py-2 bg-[var(--accent)] text-white text-sm font-medium rounded-lg hover:opacity-90 transition-opacity disabled:opacity-50"
        >
          {triggering ? "Starting..." : "Run Evaluation"}
        </button>
      </div>

      <div className="flex-1 overflow-y-auto px-6 py-6">
        <div className="max-w-4xl space-y-6">
          {error && (
            <div className="flex items-center justify-between bg-[var(--red-light)] text-[var(--red)] text-sm px-4 py-2.5 rounded-lg">
              <span>{error}</span>
              <button onClick={() => setError("")} className="text-xs hover:underline">dismiss</button>
            </div>
          )}

          {/* Summary cards */}
          {summary && summary.tier_breakdown.length > 0 && (
            <div className="bg-[var(--surface)] rounded-xl p-5 space-y-4" style={{ boxShadow: "var(--shadow-sm)" }}>
              <h3 className="text-sm font-medium text-[var(--text)]">Latest Run — Tier Breakdown</h3>
              <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
                {summary.tier_breakdown.map((tier) => (
                  <div key={tier.tier} className="border border-[var(--border-light)] rounded-lg p-4 space-y-3">
                    <div className="flex items-center justify-between">
                      <span className="text-sm font-medium text-[var(--text)] capitalize">{tier.tier}</span>
                      <span className="text-xs text-[var(--text-muted)]">{tier.count} questions</span>
                    </div>
                    <div className="grid grid-cols-3 gap-2">
                      <MetricCell label="Faith." value={tier.avg_faithfulness} scoreColor={scoreColor} scoreBg={scoreBg} />
                      <MetricCell label="Relev." value={tier.avg_relevancy} scoreColor={scoreColor} scoreBg={scoreBg} />
                      <MetricCell label="Prec." value={tier.avg_precision} scoreColor={scoreColor} scoreBg={scoreBg} />
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Runs list */}
          <div className="bg-[var(--surface)] rounded-xl p-5 space-y-3" style={{ boxShadow: "var(--shadow-sm)" }}>
            <div className="flex items-center justify-between">
              <h3 className="text-sm font-medium text-[var(--text)]">Eval Runs</h3>
              <button onClick={loadRuns} className="text-xs text-[var(--accent)] hover:underline">Refresh</button>
            </div>

            {runs.length === 0 ? (
              <p className="text-sm text-[var(--text-muted)] py-4 text-center">No evaluation runs yet. Click "Run Evaluation" to start.</p>
            ) : (
              <div className="space-y-2">
                {runs.map((run) => {
                  const isSelected = selectedRunId === run.run_id;
                  const metrics = run.metrics as Record<string, number> | null;
                  return (
                    <div key={run.run_id} className="group">
                      <button
                        onClick={() => handleSelectRun(run.run_id)}
                        className={`w-full text-left rounded-lg border p-3 transition-colors ${
                          isSelected
                            ? "border-[var(--accent)] bg-[var(--accent-light)]"
                            : "border-[var(--border-light)] hover:bg-[var(--bg-secondary)]"
                        }`}
                      >
                        <div className="flex items-center gap-3">
                          <span className={`text-[10px] px-2 py-0.5 rounded-full font-medium ${statusBadge(run.status)}`}>
                            {run.status}
                          </span>
                          <span className="text-xs font-mono text-[var(--text-muted)]">{run.run_id.slice(0, 8)}</span>
                          <span className="text-xs text-[var(--text-secondary)] flex-1">
                            {run.pair_count ?? "?"} questions
                          </span>
                          {metrics && (
                            <div className="flex gap-3 text-xs font-mono">
                              {metrics.avg_faithfulness != null && (
                                <span className={scoreColor(metrics.avg_faithfulness)}>
                                  F:{metrics.avg_faithfulness.toFixed(2)}
                                </span>
                              )}
                              {metrics.avg_answer_relevancy != null && (
                                <span className={scoreColor(metrics.avg_answer_relevancy)}>
                                  R:{metrics.avg_answer_relevancy.toFixed(2)}
                                </span>
                              )}
                              {metrics.avg_context_precision != null && (
                                <span className={scoreColor(metrics.avg_context_precision)}>
                                  P:{metrics.avg_context_precision.toFixed(2)}
                                </span>
                              )}
                            </div>
                          )}
                          <span className="text-[10px] text-[var(--text-muted)]">
                            {run.started_at ? new Date(run.started_at).toLocaleString() : "—"}
                          </span>
                          <button
                            onClick={(e) => handleDelete(e, run.run_id)}
                            className="text-[10px] text-[var(--red)] hover:underline opacity-0 group-hover:opacity-100 transition-opacity"
                            title="Delete run"
                          >
                            delete
                          </button>
                          <svg className={`w-3 h-3 text-[var(--text-muted)] transition-transform ${isSelected ? "rotate-180" : ""}`} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}>
                            <path strokeLinecap="round" strokeLinejoin="round" d="M19.5 8.25l-7.5 7.5-7.5-7.5" />
                          </svg>
                        </div>
                      </button>

                      {/* Expanded results */}
                      {isSelected && (
                        <div className="mt-2 space-y-2 pl-2">
                          {loadingResults ? (
                            <p className="text-xs text-[var(--text-muted)] py-3 text-center">Loading results...</p>
                          ) : results.length === 0 ? (
                            <p className="text-xs text-[var(--text-muted)] py-3 text-center">No results yet — run may still be in progress.</p>
                          ) : (
                            <>
                              <div className="overflow-x-auto">
                                <table className="w-full text-sm">
                                  <thead>
                                    <tr className="text-[var(--text-muted)] border-b border-[var(--border-light)]">
                                      <th className="text-left py-2 font-medium text-xs">Question</th>
                                      <th className="text-left py-2 font-medium text-xs w-16">Tier</th>
                                      <th className="text-center py-2 font-medium text-xs w-20">Faith.</th>
                                      <th className="text-center py-2 font-medium text-xs w-20">Relev.</th>
                                      <th className="text-center py-2 font-medium text-xs w-20">Prec.</th>
                                    </tr>
                                  </thead>
                                  <tbody>
                                    {results.map((r, i) => {
                                      const m = r.metrics as Record<string, number> | null;
                                      return (
                                        <ResultRow key={r.pair_id || i} result={r} metrics={m} scoreColor={scoreColor} scoreBg={scoreBg} />
                                      );
                                    })}
                                  </tbody>
                                </table>
                              </div>
                            </>
                          )}
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

function MetricCell({
  label,
  value,
  scoreColor,
  scoreBg,
}: {
  label: string;
  value: number | null;
  scoreColor: (v: number | null) => string;
  scoreBg: (v: number | null) => string;
}) {
  return (
    <div className={`rounded-lg px-2 py-2 text-center ${scoreBg(value)}`}>
      <div className={`text-lg font-semibold font-mono ${scoreColor(value)}`}>
        {value != null ? value.toFixed(2) : "—"}
      </div>
      <div className="text-[10px] text-[var(--text-muted)]">{label}</div>
    </div>
  );
}

function ResultRow({
  result,
  metrics,
  scoreColor,
  scoreBg,
}: {
  result: EvalResult;
  metrics: Record<string, number> | null;
  scoreColor: (v: number | null | undefined) => string;
  scoreBg: (v: number | null | undefined) => string;
}) {
  const [expanded, setExpanded] = useState(false);

  return (
    <>
      <tr
        className="border-b border-[var(--border-light)] last:border-0 hover:bg-[var(--bg-secondary)] cursor-pointer transition-colors"
        onClick={() => setExpanded(!expanded)}
      >
        <td className="py-2 text-[var(--text)] text-sm truncate max-w-[300px]">{result.question}</td>
        <td className="py-2">
          <span className="text-[10px] px-2 py-0.5 rounded-full bg-[var(--bg-tertiary)] text-[var(--text-secondary)]">
            {result.tier}
          </span>
        </td>
        <td className="py-2 text-center">
          <span className={`text-xs font-mono px-2 py-0.5 rounded ${scoreBg(metrics?.faithfulness)} ${scoreColor(metrics?.faithfulness)}`}>
            {metrics?.faithfulness != null ? metrics.faithfulness.toFixed(2) : "—"}
          </span>
        </td>
        <td className="py-2 text-center">
          <span className={`text-xs font-mono px-2 py-0.5 rounded ${scoreBg(metrics?.answer_relevancy)} ${scoreColor(metrics?.answer_relevancy)}`}>
            {metrics?.answer_relevancy != null ? metrics.answer_relevancy.toFixed(2) : "—"}
          </span>
        </td>
        <td className="py-2 text-center">
          <span className={`text-xs font-mono px-2 py-0.5 rounded ${scoreBg(metrics?.context_precision)} ${scoreColor(metrics?.context_precision)}`}>
            {metrics?.context_precision != null ? metrics.context_precision.toFixed(2) : "—"}
          </span>
        </td>
      </tr>
      {expanded && (
        <tr>
          <td colSpan={5} className="px-3 pb-3">
            <div className="bg-[var(--bg-secondary)] rounded-lg p-3 space-y-2 text-xs">
              {result.expected_answer && (
                <div>
                  <span className="text-[var(--text-muted)] font-medium">Expected:</span>
                  <p className="text-[var(--text-secondary)] mt-0.5 whitespace-pre-wrap">{result.expected_answer}</p>
                </div>
              )}
              {result.actual_answer && (
                <div>
                  <span className="text-[var(--text-muted)] font-medium">Actual:</span>
                  <p className="text-[var(--text-secondary)] mt-0.5 whitespace-pre-wrap">{result.actual_answer}</p>
                </div>
              )}
              {result.trace_id && (
                <div className="text-[var(--text-muted)] font-mono text-[10px]">trace: {result.trace_id}</div>
              )}
            </div>
          </td>
        </tr>
      )}
    </>
  );
}
