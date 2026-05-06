import type { PipelineStage, ToolCall } from "../types";

const STAGE_ICONS: Record<string, string> = {
  hybrid_search: "search",
  rerank: "filter",
  corrective_eval: "shield-check",
  query_retry: "refresh",
  parent_fetch: "layers",
  generation: "sparkles",
};

function StageIcon({ name, status }: { name: string; status: string }) {
  const icons: Record<string, string> = {
    search: "M21 21l-5.197-5.197m0 0A7.5 7.5 0 105.196 5.196a7.5 7.5 0 0010.607 10.607z",
    filter: "M12 3c2.755 0 5.455.232 8.083.678.533.09.917.556.917 1.096v1.044a2.25 2.25 0 01-.659 1.591l-5.432 5.432a2.25 2.25 0 00-.659 1.591v2.927a2.25 2.25 0 01-1.244 2.013L9.75 21v-6.568a2.25 2.25 0 00-.659-1.591L3.659 7.409A2.25 2.25 0 013 5.818V4.774c0-.54.384-1.006.917-1.096A48.32 48.32 0 0112 3z",
    "shield-check": "M9 12.75L11.25 15 15 9.75m-3-7.036A11.959 11.959 0 013.598 6 11.99 11.99 0 003 9.749c0 5.592 3.824 10.29 9 11.623 5.176-1.332 9-6.03 9-11.622 0-1.31-.21-2.571-.598-3.751h-.152c-3.196 0-6.1-1.248-8.25-3.285z",
    refresh: "M16.023 9.348h4.992v-.001M2.985 19.644v-4.992m0 0h4.992m-4.993 0l3.181 3.183a8.25 8.25 0 0013.803-3.7M4.031 9.865a8.25 8.25 0 0113.803-3.7l3.181 3.182m0-4.991v4.99",
    layers: "M6.429 9.75L2.25 12l4.179 2.25m0-4.5l5.571 3 5.571-3m-11.142 0L2.25 7.5 12 2.25l9.75 5.25-4.179 2.25m0 0L21.75 12l-4.179 2.25m0 0l4.179 2.25L12 21.75 2.25 16.5l4.179-2.25m11.142 0l-5.571 3-5.571-3",
    sparkles: "M9.813 15.904L9 18.75l-.813-2.846a4.5 4.5 0 00-3.09-3.09L2.25 12l2.846-.813a4.5 4.5 0 003.09-3.09L9 5.25l.813 2.846a4.5 4.5 0 003.09 3.09L15.75 12l-2.846.813a4.5 4.5 0 00-3.09 3.09zM18.259 8.715L18 9.75l-.259-1.035a3.375 3.375 0 00-2.455-2.456L14.25 6l1.036-.259a3.375 3.375 0 002.455-2.456L18 2.25l.259 1.035a3.375 3.375 0 002.455 2.456L21.75 6l-1.036.259a3.375 3.375 0 00-2.455 2.456z",
  };

  const iconKey = STAGE_ICONS[name] || "search";
  const path = icons[iconKey] || icons.search;
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

  return <span className="text-[11px] text-[var(--text-muted)] ml-1">{items.join(" · ")}</span>;
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
      ? "text-[var(--green)] bg-[var(--green-dim)]"
      : confidence === "uncertain"
        ? "text-[var(--yellow)] bg-[var(--yellow-dim)]"
        : confidence === "low_confidence"
          ? "text-[var(--red)] bg-[var(--red-dim)]"
          : "text-[var(--text-muted)] bg-[var(--bg-card)]";

  const hasActivity = stages.some((s) => s.status !== "pending") || toolCalls.length > 0;

  return (
    <div className="bg-[var(--bg-card)] border border-[var(--border)] rounded-lg p-4 space-y-3">
      <div className="flex items-center justify-between">
        <h3 className="text-xs font-medium text-[var(--text-secondary)] uppercase tracking-wider">Pipeline</h3>
        {confidence && (
          <span className={`text-[11px] px-2 py-0.5 rounded-full font-medium ${confidenceColor}`}>
            {confidence.replace("_", " ")}
          </span>
        )}
      </div>

      {!hasActivity && (
        <p className="text-xs text-[var(--text-muted)] py-4 text-center">
          Send a question to see the pipeline execute
        </p>
      )}

      {/* Pipeline stages */}
      <div className="space-y-1">
        {stages.map((stage) => (
          <div
            key={stage.name}
            className={`flex items-center gap-2 py-1.5 px-2 rounded-md transition-colors ${
              stage.status === "running"
                ? "bg-[var(--accent-dim)] stage-running"
                : stage.status === "complete"
                  ? "bg-transparent"
                  : ""
            }`}
          >
            {stage.status === "pending" ? (
              <div className="w-4 h-4 flex items-center justify-center">
                <div className="w-1.5 h-1.5 rounded-full bg-[var(--text-muted)]" />
              </div>
            ) : (
              <StageIcon name={stage.name} status={stage.status} />
            )}
            <span
              className={`text-xs flex-1 ${
                stage.status === "pending" ? "text-[var(--text-muted)]" : "text-[var(--text-primary)]"
              }`}
            >
              {stage.label}
            </span>
            {stage.duration_ms !== undefined && (
              <span className="text-[11px] text-[var(--text-muted)] font-mono tabular-nums">
                {stage.duration_ms.toFixed(0)}ms
              </span>
            )}
            <StageDetail stage={stage} />
          </div>
        ))}
      </div>

      {/* Tool calls (agent mode) */}
      {toolCalls.length > 0 && (
        <div className="space-y-1 border-t border-[var(--border)] pt-2 mt-2">
          <h4 className="text-[11px] text-[var(--text-muted)] uppercase tracking-wider mb-1">Tool Calls</h4>
          {toolCalls.map((tc, i) => (
            <div key={i} className="flex items-center gap-2 py-1 px-2 rounded-md bg-[var(--blue-dim)]">
              <svg className="w-3.5 h-3.5 text-[var(--blue)]" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" d="M11.42 15.17l-5.658 3.163 1.078-6.3-4.583-4.466 6.33-.92L11.42 1 14.14 6.647l6.33.92-4.583 4.466 1.078 6.3z" />
              </svg>
              <span className="text-xs text-[var(--text-primary)] flex-1 font-mono">
                {tc.tool_name}
              </span>
              <span className="text-[11px] text-[var(--text-muted)] font-mono tabular-nums">
                {tc.duration_ms.toFixed(0)}ms
              </span>
            </div>
          ))}
        </div>
      )}

      {/* Summary stats */}
      {totalMs !== null && (
        <div className="border-t border-[var(--border)] pt-2 mt-2 grid grid-cols-2 gap-x-4 gap-y-1">
          <Stat label="Total" value={`${totalMs.toFixed(0)}ms`} />
          {retrievalMs !== null && <Stat label="Retrieval" value={`${retrievalMs.toFixed(0)}ms`} />}
          {generationMs !== null && <Stat label="Generation" value={`${generationMs.toFixed(0)}ms`} />}
          {chunksUsed !== null && <Stat label="Chunks" value={`${chunksUsed}`} />}
          {traceId && (
            <div className="col-span-2 mt-1">
              <span className="text-[10px] text-[var(--text-muted)]">
                Trace: <span className="font-mono">{traceId.slice(0, 16)}...</span>
              </span>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex justify-between">
      <span className="text-[11px] text-[var(--text-muted)]">{label}</span>
      <span className="text-[11px] text-[var(--text-secondary)] font-mono tabular-nums">{value}</span>
    </div>
  );
}
