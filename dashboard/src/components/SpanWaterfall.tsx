import type { TraceData, Span } from "../types";

const SPAN_COLORS: Record<string, string> = {
  retrieve_pipeline: "bg-[var(--accent)]",
  hybrid_search: "bg-[var(--blue)]",
  rerank: "bg-[var(--yellow)]",
  corrective_eval: "bg-[var(--green)]",
  query_transform_retry: "bg-[var(--red)]",
  parent_fetch: "bg-[var(--cyan)]",
  generate_answer: "bg-[var(--purple)]",
  qa_request: "bg-[var(--text-muted)]",
  ws_qa_request: "bg-[var(--text-muted)]",
  ws_agent_request: "bg-[var(--text-muted)]",
  agent_turn: "bg-[var(--accent)]",
};

function getColor(name: string): string {
  for (const [key, color] of Object.entries(SPAN_COLORS)) {
    if (name.includes(key)) return color;
  }
  if (name.startsWith("tool:")) return "bg-[var(--blue)]";
  return "bg-[var(--text-muted)]";
}

function buildTree(spans: Span[]): { span: Span; depth: number }[] {
  const children = new Map<string | null, Span[]>();

  for (const s of spans) {
    const pid = s.parent_span_id;
    if (!children.has(pid)) children.set(pid, []);
    children.get(pid)!.push(s);
  }

  const result: { span: Span; depth: number }[] = [];

  function walk(parentId: string | null, depth: number) {
    const kids = children.get(parentId) || [];
    kids.sort((a, b) => a.start_ns - b.start_ns);
    for (const s of kids) {
      result.push({ span: s, depth });
      walk(s.span_id, depth + 1);
    }
  }

  walk(null, 0);

  if (result.length === 0 && spans.length > 0) {
    for (const s of spans) {
      result.push({ span: s, depth: 0 });
    }
  }

  return result;
}

export default function SpanWaterfall({ trace }: { trace: TraceData | null }) {
  if (!trace || !trace.spans || trace.spans.length === 0) return null;

  const tree = buildTree(trace.spans);
  const allStarts = trace.spans.map((s) => s.start_ns);
  const allEnds = trace.spans.map((s) => s.end_ns);
  const traceStart = Math.min(...allStarts);
  const traceEnd = Math.max(...allEnds);
  const traceDuration = traceEnd - traceStart;

  if (traceDuration <= 0) return null;

  return (
    <div
      className="bg-[var(--surface)] rounded-xl p-4 space-y-3 fade-in"
      style={{ boxShadow: "var(--shadow-sm)" }}
    >
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-medium text-[var(--text)]">Trace Waterfall</h3>
        <span className="text-xs text-[var(--text-muted)] font-mono">
          {trace.span_count} spans &middot; {trace.duration_ms?.toFixed(0)}ms
        </span>
      </div>

      <div className="space-y-0.5">
        {tree.map(({ span, depth }) => {
          const left = ((span.start_ns - traceStart) / traceDuration) * 100;
          const width = Math.max(((span.end_ns - span.start_ns) / traceDuration) * 100, 0.5);

          return (
            <div key={span.span_id} className="flex items-center gap-2 group" style={{ paddingLeft: `${depth * 16}px` }}>
              <span className="text-[11px] text-[var(--text-secondary)] w-[140px] truncate shrink-0 font-mono">
                {span.name}
              </span>
              <div className="flex-1 h-5 relative bg-[var(--bg-tertiary)] rounded overflow-hidden">
                <div
                  className={`absolute top-0.5 bottom-0.5 rounded waterfall-bar ${getColor(span.name)} opacity-70 group-hover:opacity-100 transition-opacity`}
                  style={{ left: `${left}%`, width: `${width}%` }}
                />
              </div>
              <span className="text-[11px] text-[var(--text-muted)] w-[50px] text-right shrink-0 font-mono tabular-nums">
                {span.duration_ms.toFixed(0)}ms
              </span>
            </div>
          );
        })}
      </div>
    </div>
  );
}
