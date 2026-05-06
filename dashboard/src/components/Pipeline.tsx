import type { PipelineStage, ToolCall } from "../types";

function StageIcon({ name, status }: { name: string; status: string }) {
  const icons: Record<string, string> = {
    classify: "M9.568 3H5.25A2.25 2.25 0 003 5.25v4.318c0 .597.237 1.17.659 1.591l9.581 9.581c.699.699 1.78.872 2.607.33a18.095 18.095 0 005.223-5.223c.542-.827.369-1.908-.33-2.607L11.16 3.66A2.25 2.25 0 009.568 3z",
    hybrid_search: "M21 21l-5.197-5.197m0 0A7.5 7.5 0 105.196 5.196a7.5 7.5 0 0010.607 10.607z",
    rerank: "M12 3c2.755 0 5.455.232 8.083.678.533.09.917.556.917 1.096v1.044a2.25 2.25 0 01-.659 1.591l-5.432 5.432a2.25 2.25 0 00-.659 1.591v2.927a2.25 2.25 0 01-1.244 2.013L9.75 21v-6.568a2.25 2.25 0 00-.659-1.591L3.659 7.409A2.25 2.25 0 013 5.818V4.774c0-.54.384-1.006.917-1.096A48.32 48.32 0 0112 3z",
    corrective_eval: "M9 12.75L11.25 15 15 9.75m-3-7.036A11.959 11.959 0 013.598 6 11.99 11.99 0 003 9.749c0 5.592 3.824 10.29 9 11.623 5.176-1.332 9-6.03 9-11.622 0-1.31-.21-2.571-.598-3.751h-.152c-3.196 0-6.1-1.248-8.25-3.285z",
    query_retry: "M16.023 9.348h4.992v-.001M2.985 19.644v-4.992m0 0h4.992m-4.993 0l3.181 3.183a8.25 8.25 0 0013.803-3.7M4.031 9.865a8.25 8.25 0 0113.803-3.7l3.181 3.182",
    parent_fetch: "M6.429 9.75L2.25 12l4.179 2.25m0-4.5l5.571 3 5.571-3m-11.142 0L2.25 7.5 12 2.25l9.75 5.25-4.179 2.25m0 0L21.75 12l-4.179 2.25m0 0l4.179 2.25L12 21.75 2.25 16.5l4.179-2.25m11.142 0l-5.571 3-5.571-3",
    generation: "M9.813 15.904L9 18.75l-.813-2.846a4.5 4.5 0 00-3.09-3.09L2.25 12l2.846-.813a4.5 4.5 0 003.09-3.09L9 5.25l.813 2.846a4.5 4.5 0 003.09 3.09L15.75 12l-2.846.813a4.5 4.5 0 00-3.09 3.09zM18.259 8.715L18 9.75l-.259-1.035a3.375 3.375 0 00-2.455-2.456L14.25 6l1.036-.259a3.375 3.375 0 002.455-2.456L18 2.25l.259 1.035a3.375 3.375 0 002.455 2.456L21.75 6l-1.036.259a3.375 3.375 0 00-2.455 2.456z",
  };

  const path = icons[name] || icons.hybrid_search;
  const color =
    status === "complete"
      ? "text-[var(--green)]"
      : status === "running"
        ? "text-[var(--accent)]"
        : "text-[var(--text-muted)]";

  return (
    <svg className={`w-4 h-4 ${color}`} fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
      <path strokeLinecap="round" strokeLinejoin="round" d={path} />
    </svg>
  );
}

function StageDetail({ stage }: { stage: PipelineStage }) {
  if (!stage.detail || Object.keys(stage.detail).length === 0) return null;

  const d = stage.detail;
  const items: string[] = [];

  if (d.candidates !== undefined) items.push(`${d.candidates} candidates`);
  if (d.input !== undefined && d.output !== undefined) items.push(`${d.input} → ${d.output} chunks`);
  if (d.confidence !== undefined) items.push(`${d.confidence}`);
  if (d.filtered !== undefined && d.original !== undefined) items.push(`${d.filtered}/${d.original} relevant`);
  if (d.parents !== undefined) items.push(`${d.parents} parents`);
  if (d.improved !== undefined) items.push(d.improved ? "improved" : "no improvement");
  if (d.transformed_query) items.push(`→ "${String(d.transformed_query).slice(0, 60)}"`);

  if (items.length === 0) return null;

  return <span className="text-[11px] text-[var(--text-muted)]">{items.join(" · ")}</span>;
}

interface Props {
  stages: PipelineStage[];
  toolCalls: ToolCall[];
  confidence: string | null;
  totalMs: number | null;
  traceId: string | null;
  retrievalMs: number | null;
  generationMs: number | null;
  chunksUsed: number | null;
}

export default function Pipeline({
  stages,
  toolCalls,
  confidence,
  totalMs,
  traceId,
  retrievalMs,
  generationMs,
  chunksUsed,
}: Props) {
  const confidenceColor =
    confidence === "confident"
      ? "text-[var(--green)] bg-[var(--green-light)]"
      : confidence === "uncertain"
        ? "text-[var(--yellow)] bg-[var(--yellow-light)]"
        : confidence === "low_confidence"
          ? "text-[var(--red)] bg-[var(--red-light)]"
          : "";

  const hasActivity = stages.some((s) => s.status !== "pending") || toolCalls.length > 0;

  if (!hasActivity) return null;

  return (
    <div className="space-y-2">
      {/* Stages as inline steps */}
      <div className="flex flex-wrap items-center gap-x-1 gap-y-1.5">
        {stages.map((stage, i) => (
          <div key={stage.name} className="flex items-center gap-1">
            {i > 0 && (
              <svg className="w-3 h-3 text-[var(--border)]" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M8.25 4.5l7.5 7.5-7.5 7.5" />
              </svg>
            )}
            <div
              className={`flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs transition-all ${
                stage.status === "running"
                  ? "bg-[var(--accent-light)] text-[var(--accent)] stage-running"
                  : stage.status === "complete"
                    ? "bg-[var(--green-light)] text-[var(--green)]"
                    : stage.status === "error"
                      ? "bg-[var(--red-light)] text-[var(--red)]"
                      : "bg-[var(--bg-tertiary)] text-[var(--text-muted)]"
              }`}
            >
              {stage.status === "complete" ? (
                <svg className="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2.5}>
                  <path strokeLinecap="round" strokeLinejoin="round" d="M4.5 12.75l6 6 9-13.5" />
                </svg>
              ) : stage.status === "error" ? (
                <svg className="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}>
                  <path strokeLinecap="round" strokeLinejoin="round" d="M12 9v3.75m9-.75a9 9 0 11-18 0 9 9 0 0118 0zm-9 3.75h.008v.008H12v-.008z" />
                </svg>
              ) : stage.status === "running" ? (
                <StageIcon name={stage.name} status={stage.status} />
              ) : (
                <div className="w-3.5 h-3.5 flex items-center justify-center">
                  <div className="w-1.5 h-1.5 rounded-full bg-[var(--text-muted)]" />
                </div>
              )}
              <span>{stage.label}</span>
              {stage.duration_ms !== undefined && (
                <span className="font-mono text-[10px] opacity-70">{stage.duration_ms.toFixed(0)}ms</span>
              )}
            </div>
          </div>
        ))}
        {confidence && (
          <span className={`text-xs px-2.5 py-1 rounded-full font-medium ${confidenceColor}`}>
            {confidence.replace("_", " ")}
          </span>
        )}
      </div>

      {/* Stage details */}
      {stages.filter((s) => s.status === "complete" && s.detail).map((stage) => (
        <div key={`detail-${stage.name}`} className="pl-1">
          <StageDetail stage={stage} />
        </div>
      ))}

      {/* Tool calls (agent mode) */}
      {toolCalls.length > 0 && (
        <div className="flex flex-wrap gap-1.5 pt-1">
          {toolCalls.map((tc, i) => (
            <div key={i} className="flex items-center gap-1.5 px-2.5 py-1 rounded-full bg-[var(--blue-light)] text-[var(--blue)] text-xs">
              <svg className="w-3 h-3" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" d="M21.75 6.75a4.5 4.5 0 01-4.884 4.484c-1.076-.091-2.264.071-2.95.904l-7.152 8.684a2.548 2.548 0 11-3.586-3.586l8.684-7.152c.833-.686.995-1.874.904-2.95a4.5 4.5 0 016.336-4.486l-3.276 3.276a3.004 3.004 0 002.25 2.25l3.276-3.276c.256.565.398 1.192.398 1.852z" />
              </svg>
              <span className="font-mono">{tc.tool_name}</span>
              {tc.duration_ms > 0 && (
                <span className="font-mono text-[10px] opacity-70">{tc.duration_ms.toFixed(0)}ms</span>
              )}
            </div>
          ))}
        </div>
      )}

      {/* Summary stats */}
      {totalMs !== null && (
        <div className="flex flex-wrap gap-3 pt-1 text-xs text-[var(--text-muted)]">
          <span className="font-mono">{totalMs.toFixed(0)}ms total</span>
          {retrievalMs !== null && <span className="font-mono">{retrievalMs.toFixed(0)}ms retrieval</span>}
          {generationMs !== null && <span className="font-mono">{generationMs.toFixed(0)}ms generation</span>}
          {chunksUsed !== null && <span>{chunksUsed} chunks</span>}
          {traceId && (
            <span className="font-mono text-[11px]">trace:{traceId.slice(0, 12)}</span>
          )}
        </div>
      )}
    </div>
  );
}
