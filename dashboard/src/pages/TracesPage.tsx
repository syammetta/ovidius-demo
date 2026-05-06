import { useEffect, useState } from "react";
import { fetchTraces, fetchTrace, fetchQueryLogs, fetchMetrics } from "../api";
import type { QueryLogEntry, TraceData } from "../types";
import SpanWaterfall from "../components/SpanWaterfall";

interface TraceSummary {
  trace_id: string;
  root_name: string | null;
  span_count: number;
  duration_ms: number | null;
  status: string | null;
}

export default function TracesPage() {
  const [tab, setTab] = useState<"traces" | "logs" | "metrics">("traces");
  const [traces, setTraces] = useState<TraceSummary[]>([]);
  const [logs, setLogs] = useState<QueryLogEntry[]>([]);
  const [metrics, setMetrics] = useState<Record<string, unknown> | null>(null);
  const [selectedTrace, setSelectedTrace] = useState<TraceData | null>(null);
  const [loading, setLoading] = useState(false);

  async function loadTraces() {
    setLoading(true);
    try {
      const data = await fetchTraces(30);
      setTraces(data);
    } catch {
      // ignore
    }
    setLoading(false);
  }

  async function loadLogs() {
    setLoading(true);
    try {
      const data = await fetchQueryLogs(50);
      setLogs(data);
    } catch {
      // ignore
    }
    setLoading(false);
  }

  async function loadMetrics() {
    setLoading(true);
    try {
      const data = await fetchMetrics();
      setMetrics(data);
    } catch {
      // ignore
    }
    setLoading(false);
  }

  useEffect(() => {
    if (tab === "traces") loadTraces();
    else if (tab === "logs") loadLogs();
    else loadMetrics();
  }, [tab]);

  async function openTrace(traceId: string) {
    if (selectedTrace?.trace_id === traceId) {
      setSelectedTrace(null);
      return;
    }
    try {
      const data = await fetchTrace(traceId);
      setSelectedTrace(data);
    } catch {
      // ignore
    }
  }

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="px-6 py-4 border-b border-[var(--border-light)]">
        <h1 className="text-lg font-medium text-[var(--text)]">Observability</h1>
        <p className="text-sm text-[var(--text-muted)]">OpenTelemetry traces, query logs, and metrics</p>
      </div>

      {/* Tabs */}
      <div className="px-6 border-b border-[var(--border-light)] flex gap-0">
        {(["traces", "logs", "metrics"] as const).map((t) => (
          <button
            key={t}
            onClick={() => setTab(t)}
            className={`px-4 py-2.5 text-sm capitalize transition-colors border-b-2 -mb-px ${
              tab === t
                ? "border-[var(--accent)] text-[var(--accent)] font-medium"
                : "border-transparent text-[var(--text-secondary)] hover:text-[var(--text)]"
            }`}
          >
            {t}
          </button>
        ))}
        <div className="flex-1" />
        <button
          onClick={() => {
            if (tab === "traces") loadTraces();
            else if (tab === "logs") loadLogs();
            else loadMetrics();
          }}
          disabled={loading}
          className="text-xs text-[var(--accent)] hover:underline self-center disabled:opacity-50"
        >
          Refresh
        </button>
      </div>

      {/* Content */}
      <div className="flex-1 overflow-y-auto px-6 py-4">
        {loading && (
          <div className="text-sm text-[var(--text-muted)] text-center py-8">Loading...</div>
        )}

        {/* Traces tab */}
        {tab === "traces" && !loading && (
          <div className="space-y-2">
            {traces.length === 0 && (
              <div className="text-sm text-[var(--text-muted)] text-center py-8">No traces yet</div>
            )}
            {traces.map((t) => (
              <div key={t.trace_id}>
                <div
                  onClick={() => openTrace(t.trace_id)}
                  className={`rounded-xl border px-4 py-3 cursor-pointer transition-colors ${
                    selectedTrace?.trace_id === t.trace_id
                      ? "border-[var(--accent)] bg-[var(--accent-light)]"
                      : "border-[var(--border-light)] bg-[var(--surface)] hover:border-[var(--border)]"
                  }`}
                  style={selectedTrace?.trace_id !== t.trace_id ? { boxShadow: "var(--shadow-sm)" } : undefined}
                >
                  <div className="flex items-center gap-4">
                    <span className={`text-[10px] px-2 py-0.5 rounded-full font-medium ${
                      t.status === "OK" || t.status === "UNSET"
                        ? "bg-[var(--green-light)] text-[var(--green)]"
                        : "bg-[var(--red-light)] text-[var(--red)]"
                    }`}>
                      {t.status || "OK"}
                    </span>
                    <span className="text-sm text-[var(--text)] flex-1 truncate">
                      {t.root_name || "unknown"}
                    </span>
                    <span className="text-xs text-[var(--text-muted)]">{t.span_count} spans</span>
                    <span className="text-xs font-mono text-[var(--text-muted)]">
                      {t.duration_ms?.toFixed(0)}ms
                    </span>
                    <span className="text-[10px] font-mono text-[var(--text-muted)]">
                      {t.trace_id.slice(0, 12)}
                    </span>
                  </div>
                </div>

                {/* Expanded waterfall */}
                {selectedTrace?.trace_id === t.trace_id && (
                  <div className="mt-2 ml-4">
                    <SpanWaterfall trace={selectedTrace} />
                  </div>
                )}
              </div>
            ))}
          </div>
        )}

        {/* Logs tab */}
        {tab === "logs" && !loading && (
          <div
            className="bg-[var(--surface)] rounded-xl overflow-hidden"
            style={{ boxShadow: "var(--shadow-sm)" }}
          >
            {logs.length === 0 ? (
              <div className="text-sm text-[var(--text-muted)] text-center py-8">No query logs yet</div>
            ) : (
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-[var(--border-light)] bg-[var(--bg-secondary)]">
                    <th className="text-left px-4 py-2.5 text-xs font-medium text-[var(--text-muted)]">Question</th>
                    <th className="text-left px-4 py-2.5 text-xs font-medium text-[var(--text-muted)] w-20">Conf.</th>
                    <th className="text-right px-4 py-2.5 text-xs font-medium text-[var(--text-muted)] w-20">Latency</th>
                    <th className="text-left px-4 py-2.5 text-xs font-medium text-[var(--text-muted)] w-20">Interface</th>
                    <th className="text-left px-4 py-2.5 text-xs font-medium text-[var(--text-muted)] w-28">Time</th>
                  </tr>
                </thead>
                <tbody>
                  {logs.map((log) => (
                    <tr key={log.id} className="border-b border-[var(--border-light)] last:border-0 hover:bg-[var(--bg-secondary)] transition-colors">
                      <td className="px-4 py-2.5 text-[var(--text)] truncate max-w-xs" title={log.question}>
                        {log.question}
                      </td>
                      <td className="px-4 py-2.5">
                        <ConfBadge value={log.confidence} />
                      </td>
                      <td className="px-4 py-2.5 text-right font-mono text-xs text-[var(--text-muted)] tabular-nums">
                        {log.latency_ms ? `${log.latency_ms.toFixed(0)}ms` : "—"}
                      </td>
                      <td className="px-4 py-2.5 text-xs text-[var(--text-muted)]">{log.interface}</td>
                      <td className="px-4 py-2.5 text-xs text-[var(--text-muted)]">
                        {new Date(log.created_at).toLocaleTimeString()}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        )}

        {/* Metrics tab */}
        {tab === "metrics" && !loading && (
          <div
            className="bg-[var(--surface)] rounded-xl p-5"
            style={{ boxShadow: "var(--shadow-sm)" }}
          >
            {!metrics ? (
              <div className="text-sm text-[var(--text-muted)] text-center py-8">No metrics available</div>
            ) : (
              <pre className="text-xs font-mono text-[var(--text-secondary)] whitespace-pre-wrap leading-6 overflow-x-auto">
                {JSON.stringify(metrics, null, 2)}
              </pre>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

function ConfBadge({ value }: { value: string | null }) {
  if (!value) return <span className="text-[var(--text-muted)]">—</span>;
  const c =
    value === "confident"
      ? "text-[var(--green)] bg-[var(--green-light)]"
      : value === "uncertain"
        ? "text-[var(--yellow)] bg-[var(--yellow-light)]"
        : "text-[var(--red)] bg-[var(--red-light)]";
  return <span className={`text-[10px] px-2 py-0.5 rounded-full ${c}`}>{value.replace("_", " ")}</span>;
}
