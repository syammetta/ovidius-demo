import { useState, useRef, useEffect } from "react";
import type { ConnectionStatus } from "../types";

const EXAMPLES = [
  "What is the standard deduction for a single filer in 2025?",
  "Compare Roth IRA vs Traditional IRA contribution limits",
  "How does the Child Tax Credit phase out for high earners?",
];

interface Props {
  onSubmit: (question: string, mode: "direct" | "agent") => void;
  isProcessing: boolean;
  connectionStatus: ConnectionStatus;
  onConnect: () => void;
  hideExamples?: boolean;
}

export default function QueryInput({ onSubmit, isProcessing, connectionStatus, onConnect, hideExamples }: Props) {
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
      setTimeout(() => onSubmit(q, mode), 600);
      return;
    }
    onSubmit(q, mode);
    setQuestion("");
  }

  function handleKeyDown(e: React.KeyboardEvent) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSubmit();
    }
  }

  return (
    <div className="space-y-3">
      {/* Input card */}
      <div
        className="bg-[var(--surface)] rounded-2xl px-4 pt-3 pb-2"
        style={{ boxShadow: "var(--shadow-md)" }}
      >
        <textarea
          ref={inputRef}
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="Ask a tax question..."
          rows={1}
          disabled={isProcessing}
          className="w-full bg-transparent text-[var(--text)] placeholder-[var(--text-muted)] focus:outline-none resize-none text-sm leading-6 disabled:opacity-50"
        />
        <div className="flex items-center justify-between pt-1 pb-1">
          <div className="flex items-center gap-2">
            {/* Mode toggle */}
            <div className="flex rounded-full overflow-hidden border border-[var(--border-light)]">
              <button
                onClick={() => setMode("direct")}
                className={`px-3 py-1 text-xs transition-colors ${
                  mode === "direct"
                    ? "bg-[var(--accent-light)] text-[var(--accent)] font-medium"
                    : "text-[var(--text-secondary)] hover:bg-[var(--bg-secondary)]"
                }`}
              >
                Direct QA
              </button>
              <button
                onClick={() => setMode("agent")}
                className={`px-3 py-1 text-xs transition-colors ${
                  mode === "agent"
                    ? "bg-[var(--accent-light)] text-[var(--accent)] font-medium"
                    : "text-[var(--text-secondary)] hover:bg-[var(--bg-secondary)]"
                }`}
              >
                Agent
              </button>
            </div>
            <span className="text-[11px] text-[var(--text-muted)] hidden sm:inline">
              {mode === "direct" ? "Hybrid Search + Rerank + CRAG" : "Multi-tool Agent Loop"}
            </span>
          </div>
          <button
            onClick={handleSubmit}
            disabled={isProcessing || !question.trim()}
            className="w-8 h-8 rounded-full bg-[var(--accent)] text-white flex items-center justify-center hover:opacity-90 transition-opacity disabled:opacity-30 disabled:cursor-not-allowed"
          >
            {isProcessing ? (
              <svg className="w-4 h-4 animate-spin" fill="none" viewBox="0 0 24 24">
                <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
              </svg>
            ) : (
              <svg className="w-4 h-4" viewBox="0 0 24 24" fill="currentColor">
                <path d="M3.478 2.405a.75.75 0 00-.926.94l2.432 7.905H13.5a.75.75 0 010 1.5H4.984l-2.432 7.905a.75.75 0 00.926.94 60.519 60.519 0 0018.445-8.986.75.75 0 000-1.218A60.517 60.517 0 003.478 2.405z" />
              </svg>
            )}
          </button>
        </div>
      </div>

      {/* Example chips — hidden after first query */}
      {!hideExamples && (
        <div className="flex gap-2 flex-wrap justify-center">
          {EXAMPLES.map((ex) => (
            <button
              key={ex}
              onClick={() => {
                setQuestion(ex);
                inputRef.current?.focus();
              }}
              className="text-xs px-3 py-1.5 rounded-full border border-[var(--border)] text-[var(--text-secondary)] hover:bg-[var(--bg-tertiary)] transition-colors"
            >
              {ex}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
