import { useState, useRef, useEffect } from "react";
import type { ConnectionStatus } from "../types";

const EXAMPLES = [
  "What is the standard deduction for a single filer in 2025?",
  "Compare Roth IRA vs Traditional IRA contribution limits",
  "How does the Child Tax Credit phase out for high earners?",
  "What are the EITC income limits for married filing jointly?",
  "When is the deadline to file taxes for 2025?",
];

interface Props {
  onSubmit: (question: string, mode: "direct" | "agent") => void;
  isProcessing: boolean;
  connectionStatus: ConnectionStatus;
  onConnect: () => void;
}

export default function QueryInput({ onSubmit, isProcessing, connectionStatus, onConnect }: Props) {
  const [question, setQuestion] = useState("");
  const [mode, setMode] = useState<"direct" | "agent">("direct");
  const inputRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    inputRef.current?.focus();
  }, []);

  function handleSubmit() {
    const q = question.trim();
    if (!q || isProcessing) return;
    if (connectionStatus !== "connected") {
      onConnect();
      setTimeout(() => onSubmit(q, mode), 500);
      return;
    }
    onSubmit(q, mode);
  }

  function handleKeyDown(e: React.KeyboardEvent) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSubmit();
    }
  }

  const statusColor =
    connectionStatus === "connected"
      ? "bg-[var(--green)]"
      : connectionStatus === "connecting"
        ? "bg-[var(--yellow)]"
        : "bg-[var(--text-muted)]";

  return (
    <div className="space-y-3">
      {/* Connection + Mode */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <button
            onClick={onConnect}
            className="flex items-center gap-2 px-3 py-1.5 text-xs rounded-md border border-[var(--border)] hover:border-[var(--border-active)] transition-colors"
          >
            <span className={`w-2 h-2 rounded-full ${statusColor}`} />
            {connectionStatus === "connected" ? "Connected" : "Connect"}
          </button>
          <div className="flex rounded-md overflow-hidden border border-[var(--border)]">
            <button
              onClick={() => setMode("direct")}
              className={`px-3 py-1.5 text-xs transition-colors ${
                mode === "direct"
                  ? "bg-[var(--accent-dim)] text-[var(--accent)] border-r border-[var(--border)]"
                  : "text-[var(--text-secondary)] hover:text-[var(--text-primary)] border-r border-[var(--border)]"
              }`}
            >
              Direct QA
            </button>
            <button
              onClick={() => setMode("agent")}
              className={`px-3 py-1.5 text-xs transition-colors ${
                mode === "agent"
                  ? "bg-[var(--accent-dim)] text-[var(--accent)]"
                  : "text-[var(--text-secondary)] hover:text-[var(--text-primary)]"
              }`}
            >
              Agent
            </button>
          </div>
        </div>
        <span className="text-[11px] text-[var(--text-muted)]">
          {mode === "direct" ? "Hybrid Search + Rerank + CRAG + Generate" : "Multi-tool Agent Loop"}
        </span>
      </div>

      {/* Input */}
      <div className="relative">
        <textarea
          ref={inputRef}
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="Ask a tax question..."
          rows={2}
          disabled={isProcessing}
          className="w-full bg-[var(--bg-card)] border border-[var(--border)] rounded-lg px-4 py-3 pr-24 text-[var(--text-primary)] placeholder-[var(--text-muted)] focus:outline-none focus:border-[var(--accent)] resize-none text-sm disabled:opacity-50"
        />
        <button
          onClick={handleSubmit}
          disabled={isProcessing || !question.trim()}
          className="absolute right-3 bottom-3 px-4 py-1.5 bg-[var(--accent)] text-white text-xs font-medium rounded-md hover:opacity-90 transition-opacity disabled:opacity-30 disabled:cursor-not-allowed"
        >
          {isProcessing ? "Running..." : "Send"}
        </button>
      </div>

      {/* Examples */}
      <div className="flex gap-2 flex-wrap">
        {EXAMPLES.slice(0, 3).map((ex) => (
          <button
            key={ex}
            onClick={() => {
              setQuestion(ex);
              inputRef.current?.focus();
            }}
            className="text-[11px] px-2.5 py-1 rounded-full border border-[var(--border)] text-[var(--text-muted)] hover:text-[var(--text-secondary)] hover:border-[var(--border-active)] transition-colors truncate max-w-[280px]"
          >
            {ex}
          </button>
        ))}
      </div>
    </div>
  );
}
