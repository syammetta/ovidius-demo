import type { RetrievalDetail, PipelineStage } from "../types";

interface Props {
  detail: RetrievalDetail | undefined;
  stages: PipelineStage[];
}

const DOC_TYPE_LABELS: Record<string, string> = {
  narrative: "Narrative Guide",
  api_reference: "Table / Reference",
  code_heavy: "Code / Worksheet",
};

const INTENT_LABELS: Record<string, { label: string; color: string }> = {
  factual: { label: "Factual Lookup", color: "text-[var(--accent)] bg-[var(--accent-light)]" },
  comparison: { label: "Comparison", color: "text-[var(--yellow)] bg-[var(--yellow-light)]" },
  calculation: { label: "Calculation", color: "text-[var(--green)] bg-[var(--green-light)]" },
  procedural: { label: "Procedural", color: "text-purple-700 bg-purple-50" },
  complex: { label: "Complex / Multi-hop", color: "text-[var(--red)] bg-[var(--red-light)]" },
};

export default function RetrievalInsight({ detail, stages }: Props) {
  if (!detail && !stages.some((s) => s.status !== "pending")) return null;

  const confidenceColor =
    detail?.confidence === "confident"
      ? "text-[var(--green)] bg-[var(--green-light)]"
      : detail?.confidence === "uncertain"
        ? "text-[var(--yellow)] bg-[var(--yellow-light)]"
        : detail?.confidence === "low_confidence"
          ? "text-[var(--red)] bg-[var(--red-light)]"
          : "text-[var(--text-muted)] bg-[var(--bg-tertiary)]";

  const clsStage = stages.find((s) => s.name === "classify");
  const hsStage = stages.find((s) => s.name === "hybrid_search");
  const rrStage = stages.find((s) => s.name === "rerank");
  const ceStage = stages.find((s) => s.name === "corrective_eval");
  const qrStage = stages.find((s) => s.name === "query_retry");
  const pfStage = stages.find((s) => s.name === "parent_fetch");

  const cls = detail?.classification;
  const strat = detail?.strategy;

  return (
    <div
      className="bg-[var(--surface)] rounded-xl p-4 space-y-3"
      style={{ boxShadow: "var(--shadow-sm)" }}
    >
      <h3 className="text-sm font-medium text-[var(--text)]">Retrieval Insights</h3>

      <div className="space-y-2.5 text-xs">
        {/* Query Classification */}
        {clsStage?.status === "complete" && cls && (
          <div className="space-y-1.5 pb-2 border-b border-[var(--border-light)]">
            <div className="flex items-center gap-2 text-[var(--text-secondary)]">
              <svg className="w-3.5 h-3.5 text-[var(--green)]" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" d="M9.568 3H5.25A2.25 2.25 0 003 5.25v4.318c0 .597.237 1.17.659 1.591l9.581 9.581c.699.699 1.78.872 2.607.33a18.095 18.095 0 005.223-5.223c.542-.827.369-1.908-.33-2.607L11.16 3.66A2.25 2.25 0 009.568 3z" />
              </svg>
              <span className="font-medium">Query Classification</span>
              {clsStage.duration_ms !== undefined && (
                <span className="font-mono text-[var(--text-muted)]">{clsStage.duration_ms.toFixed(0)}ms</span>
              )}
            </div>
            <div className="pl-5.5 space-y-1.5">
              <div className="flex flex-wrap gap-1.5">
                <span className={`px-2 py-0.5 rounded-full text-[10px] font-medium ${INTENT_LABELS[cls.intent]?.color || "bg-[var(--bg-tertiary)] text-[var(--text-secondary)]"}`}>
                  {INTENT_LABELS[cls.intent]?.label || cls.intent}
                </span>
                {cls.topics.map((t) => (
                  <span key={t} className="px-2 py-0.5 rounded-full text-[10px] bg-[var(--bg-tertiary)] text-[var(--text-secondary)] capitalize">
                    {t.replace("_", " ")}
                  </span>
                ))}
              </div>
              {cls.reasoning && (
                <p className="text-[var(--text-muted)] italic">{cls.reasoning}</p>
              )}
            </div>
          </div>
        )}

        {/* Strategy */}
        {strat && (
          <div className="flex items-center gap-2 text-[var(--text-secondary)]">
            <svg className="w-3.5 h-3.5 text-[var(--accent)]" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" d="M3.75 13.5l10.5-11.25L12 10.5h8.25L9.75 21.75 12 13.5H3.75z" />
            </svg>
            <span className="font-medium">Strategy: {strat.name}</span>
            <span className="text-[var(--text-muted)] font-mono">
              n={strat.top_n} k={strat.top_k}
            </span>
            {strat.metadata_boost && (
              <span className="px-1.5 py-0.5 rounded text-[10px] bg-[var(--accent-light)] text-[var(--accent)]">
                meta-boost
              </span>
            )}
          </div>
        )}

        {/* Hybrid Search breakdown */}
        {hsStage?.status === "complete" && hsStage.detail && (
          <div className="space-y-1">
            <div className="flex items-center gap-2 text-[var(--text-secondary)]">
              <svg className="w-3.5 h-3.5 text-[var(--green)]" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" d="M21 21l-5.197-5.197m0 0A7.5 7.5 0 105.196 5.196a7.5 7.5 0 0010.607 10.607z" />
              </svg>
              <span className="font-medium">
                {(hsStage.detail.lanes as number) === 3 ? "3-Lane" : "2-Lane"} Hybrid Search
              </span>
              {hsStage.duration_ms !== undefined && (
                <span className="font-mono text-[var(--text-muted)]">{hsStage.duration_ms.toFixed(0)}ms</span>
              )}
            </div>
            <div className="pl-5.5 flex flex-wrap gap-x-3 gap-y-1 text-[var(--text-muted)]">
              {typeof hsStage.detail.vector_hits === "number" && (
                <span><span className="font-mono text-[var(--accent)]">{hsStage.detail.vector_hits as number}</span> semantic</span>
              )}
              {typeof hsStage.detail.bm25_hits === "number" && (
                <span><span className="font-mono text-[var(--accent)]">{hsStage.detail.bm25_hits as number}</span> keyword</span>
              )}
              {typeof hsStage.detail.both_hits === "number" && (
                <span><span className="font-mono text-[var(--green)]">{hsStage.detail.both_hits as number}</span> overlap</span>
              )}
              {typeof hsStage.detail.metadata_boosted === "number" && (hsStage.detail.metadata_boosted as number) > 0 && (
                <span><span className="font-mono text-purple-600">{hsStage.detail.metadata_boosted as number}</span> meta-boosted</span>
              )}
              {typeof hsStage.detail.candidates === "number" && (
                <span><span className="font-mono">{hsStage.detail.candidates as number}</span> fused via RRF</span>
              )}
            </div>
          </div>
        )}

        {/* Rerank */}
        {rrStage?.status === "complete" && rrStage.detail && (
          <div className="flex items-center gap-2 text-[var(--text-secondary)]">
            <svg className="w-3.5 h-3.5 text-[var(--green)]" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" d="M12 3c2.755 0 5.455.232 8.083.678.533.09.917.556.917 1.096v1.044a2.25 2.25 0 01-.659 1.591l-5.432 5.432a2.25 2.25 0 00-.659 1.591v2.927a2.25 2.25 0 01-1.244 2.013L9.75 21v-6.568a2.25 2.25 0 00-.659-1.591L3.659 7.409A2.25 2.25 0 013 5.818V4.774c0-.54.384-1.006.917-1.096A48.32 48.32 0 0112 3z" />
            </svg>
            <span className="font-medium">Cross-Encoder Rerank</span>
            {typeof rrStage.detail.input === "number" && typeof rrStage.detail.output === "number" && (
              <span className="text-[var(--text-muted)] font-mono">
                {rrStage.detail.input as number} → {rrStage.detail.output as number}
              </span>
            )}
            {rrStage.duration_ms !== undefined && (
              <span className="font-mono text-[var(--text-muted)]">{rrStage.duration_ms.toFixed(0)}ms</span>
            )}
          </div>
        )}

        {/* CRAG evaluation */}
        {ceStage?.status === "complete" && (
          <div className="space-y-1">
            <div className="flex items-center gap-2 text-[var(--text-secondary)]">
              <svg className="w-3.5 h-3.5 text-[var(--green)]" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" d="M9 12.75L11.25 15 15 9.75m-3-7.036A11.959 11.959 0 013.598 6 11.99 11.99 0 003 9.749c0 5.592 3.824 10.29 9 11.623 5.176-1.332 9-6.03 9-11.622 0-1.31-.21-2.571-.598-3.751h-.152c-3.196 0-6.1-1.248-8.25-3.285z" />
              </svg>
              <span className="font-medium">Corrective RAG</span>
              {detail && (
                <span className={`px-2 py-0.5 rounded-full text-[10px] font-medium ${confidenceColor}`}>
                  {detail.confidence.replace("_", " ")}
                </span>
              )}
            </div>
            {detail && (
              <div className="pl-5.5 text-[var(--text-muted)]">
                <span className="font-mono">{detail.filtered}</span> chunks relevant
                <span className="mx-1">&middot;</span>
                relevance ratio <span className="font-mono text-[var(--text-secondary)]">{(detail.relevance_ratio * 100).toFixed(0)}%</span>
              </div>
            )}
          </div>
        )}

        {/* Query retry */}
        {qrStage?.status === "complete" && qrStage.detail && (
          <div className="space-y-1">
            <div className="flex items-center gap-2 text-[var(--text-secondary)]">
              <svg className="w-3.5 h-3.5 text-[var(--yellow)]" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" d="M16.023 9.348h4.992v-.001M2.985 19.644v-4.992m0 0h4.992m-4.993 0l3.181 3.183a8.25 8.25 0 0013.803-3.7M4.031 9.865a8.25 8.25 0 0113.803-3.7l3.181 3.182" />
              </svg>
              <span className="font-medium">Query Retry</span>
              {qrStage.detail.improved ? (
                <span className="text-[var(--green)] text-[10px]">improved</span>
              ) : (
                <span className="text-[var(--text-muted)] text-[10px]">no improvement</span>
              )}
            </div>
            {qrStage.detail.transformed_query != null && (
              <div className="pl-5.5 text-[var(--text-muted)] italic truncate">
                &ldquo;{String(qrStage.detail.transformed_query).slice(0, 80)}&rdquo;
              </div>
            )}
          </div>
        )}

        {/* Parent chunk expansion */}
        {pfStage?.status === "complete" && pfStage.detail && (
          <div className="flex items-center gap-2 text-[var(--text-secondary)]">
            <svg className="w-3.5 h-3.5 text-[var(--green)]" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" d="M6.429 9.75L2.25 12l4.179 2.25m0-4.5l5.571 3 5.571-3m-11.142 0L2.25 7.5 12 2.25l9.75 5.25-4.179 2.25m0 0L21.75 12l-4.179 2.25m0 0l4.179 2.25L12 21.75 2.25 16.5l4.179-2.25m11.142 0l-5.571 3-5.571-3" />
            </svg>
            <span className="font-medium">Parent Expansion</span>
            {typeof pfStage.detail.parents === "number" && (
              <span className="text-[var(--text-muted)] font-mono">
                {pfStage.detail.parents as number} parent{(pfStage.detail.parents as number) !== 1 ? "s" : ""}
              </span>
            )}
            {pfStage.duration_ms !== undefined && (
              <span className="font-mono text-[var(--text-muted)]">{pfStage.duration_ms.toFixed(0)}ms</span>
            )}
          </div>
        )}

        {/* Document types */}
        {detail && Object.keys(detail.doc_types).length > 0 && (
          <div className="space-y-1 pt-1 border-t border-[var(--border-light)]">
            <span className="text-[var(--text-secondary)] font-medium">Source Document Types</span>
            <div className="flex flex-wrap gap-1.5">
              {Object.entries(detail.doc_types).map(([type, count]) => (
                <span
                  key={type}
                  className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full bg-[var(--bg-tertiary)] text-[var(--text-secondary)]"
                >
                  <span className="font-mono text-[var(--accent)]">{count}</span>
                  {DOC_TYPE_LABELS[type] || type}
                </span>
              ))}
            </div>
          </div>
        )}

        {/* Sources */}
        {detail && detail.sources.length > 0 && (
          <div className="space-y-1 pt-1 border-t border-[var(--border-light)]">
            <span className="text-[var(--text-secondary)] font-medium">Retrieved Sources</span>
            <div className="space-y-1">
              {detail.sources.map((s, i) => (
                <div key={i} className="flex items-center gap-2 text-[var(--text-muted)]">
                  <span className="text-[var(--accent)] font-mono">{i + 1}</span>
                  <span className="truncate flex-1">{s.title}</span>
                  <span className="text-[10px] px-1.5 py-0.5 rounded bg-[var(--bg-tertiary)]">{s.type}</span>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
