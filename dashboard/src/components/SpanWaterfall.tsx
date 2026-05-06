import type { TraceData, Span } from "../types";

interface Props {
  trace: TraceData | null;
}

const SPAN_COLORS: Record<string, string> = {
  retrieve_pipeline: "bg-[var(--accent)]",
  hybrid_search: "bg-[var(--blue)]",
  rerank: "bg-[var(--yellow)]",
  corrective_eval: "bg-[var(--green)]",
  query_transform_retry: "bg-[var(--red)]",
  parent_fetch: "bg-cyan-400",
  generate_answer: "bg-purple-400",
  qa_request: "bg-[var(--text-secondary)]",
  ws_qa_request: "bg-[var(--text-secondary)]",
  ws_agent_request: "bg-[var(--text-secondary)]",
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

export default function SpanWaterfall({ trace }: Props) {
  if (!trace || !trace.spans || trace.spans.length === 0) {
    return null;
  }

  const tree = buildTree(trace.spans);
  const allStarts = trace.spans.map((s) => s.start_ns);
  const allEnds = trace.spans.map((s) => s.end_ns);
  const traceStart = Math.min(...allStarts);
  const traceEnd = Math.max(...allEnds);
  const traceDuration = traceEnd - traceStart;

  if (traceDuration <= 0) return null;

  return (
    <div className="bg-[var(--bg-card)] border border-[var(--border)] rounded-lg p-4 space-y-2">
      <div className="flex items-center justify-between">
        <h3 className="text-xs font-medium text-[var(--text-secondary)] uppercase tracking-wider">
          Trace Waterfall
        </h3>
        <span className="text-[10px] text-[var(--text-muted)] font-mono">
          {trace.trace_id.slice(0, 16)}... · {trace.span_count} spans · {trace.duration_ms?.toFixed(0)}ms
        </span>
      </div>

      <div className="space-y-0.5">
        {tree.map(({ span, depth }) => {
          const left = ((span.start_ns - traceStart) / traceDuration) * 100;
          const width = Math.max(((span.end_ns - span.start_ns) / traceDuration) * 100, 0.5);

          return (
            <div key={span.span_id} className="flex items-center gap-2 group" style={{ paddingLeft: `${depth * 16}px` }}>
              <span className="text-[10px] text-[var(--text-muted)] w-[140px] truncate shrink-0 font-mono">
                {span.name}
              </span>
              <div className="flex-1 h-5 relative bg-[var(--bg-primary)] rounded-sm overflow-hidden">
                <div
                  className={`absolute top-0.5 bottom-0.5 rounded-sm waterfall-bar ${getColor(span.name)} opacity-80 group-hover:opacity-100 transition-opacity`}
                  style={{ left: `${left}%`, width: `${width}%` }}
                />
              </div>
              <span className="text-[10px] text-[var(--text-muted)] w-[50px] text-right shrink-0 font-mono tabular-nums">
                {span.duration_ms.toFixed(0)}ms
              </span>
            </div>
          );
        })}
      </div>
    </div>
  );
}
