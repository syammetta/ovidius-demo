import { useEffect, useState } from "react";
import { fetchQueryLogs } from "../api";
import type { QueryLogEntry } from "../types";

export default function History() {
  const [logs, setLogs] = useState<QueryLogEntry[]>([]);
  const [loading, setLoading] = useState(false);

  async function load() {
    setLoading(true);
    try {
      const data = await fetchQueryLogs(10);
      setLogs(data);
    } catch {
      // ignore
    }
    setLoading(false);
  }

  useEffect(() => {
    load();
  }, []);

  if (logs.length === 0 && !loading) return null;

  return (
    <div
      className="bg-[var(--surface)] rounded-xl p-4 space-y-3"
      style={{ boxShadow: "var(--shadow-sm)" }}
    >
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-medium text-[var(--text)]">Recent Queries</h3>
        <button
          onClick={load}
          disabled={loading}
          className="text-xs text-[var(--accent)] hover:underline transition-colors disabled:opacity-50"
        >
          {loading ? "Loading..." : "Refresh"}
        </button>
      </div>

      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-[var(--text-muted)] border-b border-[var(--border-light)]">
              <th className="text-left py-2 font-medium text-xs">Question</th>
              <th className="text-left py-2 font-medium text-xs w-[80px]">Confidence</th>
              <th className="text-right py-2 font-medium text-xs w-[70px]">Latency</th>
              <th className="text-left py-2 font-medium text-xs w-[60px]">Source</th>
            </tr>
          </thead>
          <tbody>
            {logs.map((log) => (
              <tr key={log.id} className="border-b border-[var(--border-light)] last:border-0 hover:bg-[var(--bg-secondary)] transition-colors">
                <td className="py-2.5 text-[var(--text)] text-sm truncate max-w-[300px]">{log.question}</td>
                <td className="py-2.5">
                  <ConfidenceBadge value={log.confidence} />
                </td>
                <td className="py-2.5 text-right text-[var(--text-muted)] font-mono text-xs tabular-nums">
                  {log.latency_ms ? `${log.latency_ms.toFixed(0)}ms` : "—"}
                </td>
                <td className="py-2.5 text-[var(--text-muted)] text-xs">{log.interface}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function ConfidenceBadge({ value }: { value: string | null }) {
  if (!value) return <span className="text-[var(--text-muted)]">{"—"}</span>;

  const color =
    value === "confident"
      ? "text-[var(--green)] bg-[var(--green-light)]"
      : value === "uncertain"
        ? "text-[var(--yellow)] bg-[var(--yellow-light)]"
        : "text-[var(--red)] bg-[var(--red-light)]";

  return (
    <span className={`text-xs px-2 py-0.5 rounded-full ${color}`}>
      {value.replace("_", " ")}
    </span>
  );
}
