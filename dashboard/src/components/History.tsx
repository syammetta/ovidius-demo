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
    <div className="bg-[var(--bg-card)] border border-[var(--border)] rounded-lg p-4 space-y-2">
      <div className="flex items-center justify-between">
        <h3 className="text-xs font-medium text-[var(--text-secondary)] uppercase tracking-wider">
          Recent Queries
        </h3>
        <button
          onClick={load}
          disabled={loading}
          className="text-[10px] text-[var(--text-muted)] hover:text-[var(--text-secondary)] transition-colors"
        >
          {loading ? "Loading..." : "Refresh"}
        </button>
      </div>

      <div className="overflow-x-auto">
        <table className="w-full text-[11px]">
          <thead>
            <tr className="text-[var(--text-muted)] border-b border-[var(--border)]">
              <th className="text-left py-1.5 font-medium">Question</th>
              <th className="text-left py-1.5 font-medium w-[70px]">Conf.</th>
              <th className="text-right py-1.5 font-medium w-[60px]">Latency</th>
              <th className="text-left py-1.5 font-medium w-[60px]">Source</th>
            </tr>
          </thead>
          <tbody>
            {logs.map((log) => (
              <tr key={log.id} className="border-b border-[var(--border)] border-opacity-50 hover:bg-[var(--bg-card-hover)] transition-colors">
                <td className="py-1.5 text-[var(--text-primary)] truncate max-w-[300px]">{log.question}</td>
                <td className="py-1.5">
                  <ConfidenceBadge value={log.confidence} />
                </td>
                <td className="py-1.5 text-right text-[var(--text-muted)] font-mono tabular-nums">
                  {log.latency_ms ? `${log.latency_ms.toFixed(0)}ms` : "—"}
                </td>
                <td className="py-1.5 text-[var(--text-muted)]">{log.interface}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function ConfidenceBadge({ value }: { value: string | null }) {
  if (!value) return <span className="text-[var(--text-muted)]">—</span>;

  const color =
    value === "confident"
      ? "text-[var(--green)] bg-[var(--green-dim)]"
      : value === "uncertain"
        ? "text-[var(--yellow)] bg-[var(--yellow-dim)]"
        : "text-[var(--red)] bg-[var(--red-dim)]";

  return (
    <span className={`text-[10px] px-1.5 py-0.5 rounded-full ${color}`}>
      {value.replace("_", " ")}
    </span>
  );
}
