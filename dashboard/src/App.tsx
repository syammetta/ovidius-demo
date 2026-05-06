import { useCallback, useEffect, useRef, useState } from "react";
import { useQASocket, fetchHealth } from "./api";
import type {
  PipelineStage,
  Citation,
  ToolCall,
  TraceData,
  WSEvent,
} from "./types";

import QueryInput from "./components/QueryInput";
import Pipeline from "./components/Pipeline";
import Answer from "./components/Answer";
import SpanWaterfall from "./components/SpanWaterfall";
import History from "./components/History";

const DEFAULT_STAGES: PipelineStage[] = [
  { name: "hybrid_search", label: "Hybrid Search", status: "pending" },
  { name: "rerank", label: "Cross-Encoder Rerank", status: "pending" },
  { name: "corrective_eval", label: "Corrective RAG Eval", status: "pending" },
  { name: "parent_fetch", label: "Parent Chunk Expansion", status: "pending" },
  { name: "generation", label: "Answer Generation", status: "pending" },
];

function App() {
  const [stages, setStages] = useState<PipelineStage[]>(DEFAULT_STAGES);
  const [answer, setAnswer] = useState("");
  const [isStreaming, setIsStreaming] = useState(false);
  const [citations, setCitations] = useState<Citation[]>([]);
  const [confidence, setConfidence] = useState<string | null>(null);
  const [totalMs, setTotalMs] = useState<number | null>(null);
  const [retrievalMs, setRetrievalMs] = useState<number | null>(null);
  const [generationMs, setGenerationMs] = useState<number | null>(null);
  const [chunksUsed, setChunksUsed] = useState<number | null>(null);
  const [traceId, setTraceId] = useState<string | null>(null);
  const [trace, setTrace] = useState<TraceData | null>(null);
  const [toolCalls, setToolCalls] = useState<ToolCall[]>([]);
  const [isProcessing, setIsProcessing] = useState(false);
  const [health, setHealth] = useState<{ child_chunks: number; parent_chunks: number } | null>(null);
  const [historyKey, setHistoryKey] = useState(0);
  const [lastQuestion, setLastQuestion] = useState("");
  const answerRef = useRef("");

  useEffect(() => {
    fetchHealth().then(setHealth);
  }, []);

  const handleEvent = useCallback((event: WSEvent) => {
    switch (event.type) {
      case "start":
        setTraceId(event.trace_id);
        break;

      case "stage": {
        const stageName = event.stage;
        const status = event.status as "running" | "complete";
        setStages((prev) =>
          prev.map((s) =>
            s.name === stageName
              ? {
                  ...s,
                  status,
                  duration_ms: typeof event.duration_ms === "number" ? event.duration_ms : s.duration_ms,
                  detail: { ...s.detail, ...event },
                }
              : s,
          ),
        );
        break;
      }

      case "retrieval_complete":
        setConfidence(event.confidence);
        break;

      case "tool_call":
        setToolCalls((prev) => [
          ...prev,
          { tool_name: event.tool_name, tool_input: event.tool_input, duration_ms: 0 },
        ]);
        break;

      case "tool_result":
        setToolCalls((prev) => {
          const updated = [...prev];
          const last = updated.findLast((tc) => tc.tool_name === event.tool_name);
          if (last) {
            last.duration_ms = event.duration_ms;
            last.result_preview = event.result_preview;
          }
          return updated;
        });
        break;

      case "text_delta":
        answerRef.current += event.text;
        setAnswer(answerRef.current);
        break;

      case "done": {
        setIsStreaming(false);
        setIsProcessing(false);
        const d = event as Record<string, unknown>;
        if (d.citations) setCitations(d.citations as Citation[]);
        if (d.confidence) setConfidence(d.confidence as string);
        if (typeof d.total_ms === "number") setTotalMs(d.total_ms);
        if (typeof d.retrieval_ms === "number") setRetrievalMs(d.retrieval_ms);
        if (typeof d.generation_ms === "number") setGenerationMs(d.generation_ms);
        if (typeof d.chunks_used === "number") setChunksUsed(d.chunks_used);
        if (d.trace) setTrace(d.trace as TraceData);
        setHistoryKey((k) => k + 1);
        break;
      }

      case "error":
        setIsStreaming(false);
        setIsProcessing(false);
        setAnswer(`Error: ${event.message}`);
        break;
    }
  }, []);

  const { status, connect, send } = useQASocket(handleEvent);

  function handleSubmit(question: string, mode: "direct" | "agent") {
    setStages(DEFAULT_STAGES.map((s) => ({ ...s, status: "pending", duration_ms: undefined, detail: undefined })));
    setAnswer("");
    answerRef.current = "";
    setCitations([]);
    setConfidence(null);
    setTotalMs(null);
    setRetrievalMs(null);
    setGenerationMs(null);
    setChunksUsed(null);
    setTraceId(null);
    setTrace(null);
    setToolCalls([]);
    setIsStreaming(true);
    setIsProcessing(true);
    setLastQuestion(question);

    send(question, mode);
  }

  const hasResult = answer || stages.some((s) => s.status !== "pending");

  return (
    <div className="min-h-screen flex flex-col">
      {/* Header */}
      <header className="bg-[var(--surface)] border-b border-[var(--border-light)] px-6 py-3 flex items-center justify-between">
        <div className="flex items-center gap-3">
          <svg className="w-6 h-6 text-[var(--accent)]" viewBox="0 0 24 24" fill="currentColor">
            <path d="M12 6.042A8.967 8.967 0 006 3.75c-1.052 0-2.062.18-3 .512v14.25A8.987 8.987 0 016 18c2.305 0 4.408.867 6 2.292m0-14.25a8.966 8.966 0 016-2.292c1.052 0 2.062.18 3 .512v14.25A8.987 8.987 0 0018 18a8.967 8.967 0 00-6 2.292m0-14.25v14.25" />
          </svg>
          <span className="text-base font-medium text-[var(--text)]">Ovidius</span>
          <span className="text-sm text-[var(--text-muted)] hidden sm:inline">IRS Tax Documentation QA</span>
        </div>
        <div className="flex items-center gap-4">
          {health && (
            <span className="text-xs text-[var(--text-muted)]">
              {health.child_chunks.toLocaleString()} chunks &middot; {health.parent_chunks.toLocaleString()} parents
            </span>
          )}
          <div className="flex items-center gap-1.5">
            <div className={`w-2 h-2 rounded-full ${
              status === "connected" ? "bg-[var(--green)]" : "bg-[var(--text-muted)]"
            }`} />
            <span className="text-xs text-[var(--text-muted)] capitalize">{status}</span>
          </div>
        </div>
      </header>

      {/* Main content */}
      <main className="flex-1 flex flex-col max-w-4xl mx-auto w-full px-4 py-6">
        {/* Empty state */}
        {!hasResult && !isProcessing && (
          <div className="flex-1 flex items-center justify-center">
            <div className="text-center space-y-3">
              <svg className="w-10 h-10 text-[var(--accent)] mx-auto opacity-60" viewBox="0 0 24 24" fill="currentColor">
                <path d="M9.813 15.904L9 18.75l-.813-2.846a4.5 4.5 0 00-3.09-3.09L2.25 12l2.846-.813a4.5 4.5 0 003.09-3.09L9 5.25l.813 2.846a4.5 4.5 0 003.09 3.09L15.75 12l-2.846.813a4.5 4.5 0 00-3.09 3.09zM18.259 8.715L18 9.75l-.259-1.035a3.375 3.375 0 00-2.455-2.456L14.25 6l1.036-.259a3.375 3.375 0 002.455-2.456L18 2.25l.259 1.035a3.375 3.375 0 002.455 2.456L21.75 6l-1.036.259a3.375 3.375 0 00-2.455 2.456z" />
              </svg>
              <p className="text-lg text-[var(--text-secondary)]">What tax question can I help with?</p>
              <p className="text-sm text-[var(--text-muted)]">
                Ask about deductions, credits, filing deadlines, and more
              </p>
            </div>
          </div>
        )}

        {/* Conversation area */}
        {(hasResult || isProcessing) && (
          <div className="flex-1 space-y-5 pb-4 fade-in">
            {/* User question bubble */}
            {lastQuestion && (
              <div className="flex justify-end">
                <div className="bg-[var(--accent)] text-white px-4 py-2.5 rounded-2xl rounded-br-sm max-w-lg text-sm">
                  {lastQuestion}
                </div>
              </div>
            )}

            {/* AI response area */}
            <div className="flex gap-3">
              <div className="w-7 h-7 rounded-full bg-[var(--accent-light)] flex items-center justify-center shrink-0 mt-0.5">
                <svg className="w-4 h-4 text-[var(--accent)]" viewBox="0 0 24 24" fill="currentColor">
                  <path d="M9.813 15.904L9 18.75l-.813-2.846a4.5 4.5 0 00-3.09-3.09L2.25 12l2.846-.813a4.5 4.5 0 003.09-3.09L9 5.25l.813 2.846a4.5 4.5 0 003.09 3.09L15.75 12l-2.846.813a4.5 4.5 0 00-3.09 3.09zM18.259 8.715L18 9.75l-.259-1.035a3.375 3.375 0 00-2.455-2.456L14.25 6l1.036-.259a3.375 3.375 0 002.455-2.456L18 2.25l.259 1.035a3.375 3.375 0 002.455 2.456L21.75 6l-1.036.259a3.375 3.375 0 00-2.455 2.456z" />
                </svg>
              </div>
              <div className="flex-1 space-y-4 min-w-0">
                {/* Pipeline progress */}
                <Pipeline
                  stages={stages}
                  toolCalls={toolCalls}
                  confidence={confidence}
                  totalMs={totalMs}
                  traceId={traceId}
                  retrievalMs={retrievalMs}
                  generationMs={generationMs}
                  chunksUsed={chunksUsed}
                />

                {/* Answer */}
                <Answer
                  text={answer}
                  isStreaming={isStreaming}
                  citations={citations}
                  confidence={confidence}
                />
              </div>
            </div>

            {/* Trace waterfall */}
            <SpanWaterfall trace={trace} />
          </div>
        )}

        {/* Input area — bottom */}
        <div className="mt-auto pt-4">
          <QueryInput
            onSubmit={handleSubmit}
            isProcessing={isProcessing}
            connectionStatus={status}
            onConnect={connect}
          />
        </div>
      </main>

      {/* History section */}
      <div className="max-w-4xl mx-auto w-full px-4 pb-6">
        <History key={historyKey} />
      </div>

      {/* Footer */}
      <footer className="py-3 text-center">
        <span className="text-xs text-[var(--text-muted)]">
          Hybrid Search &middot; Cross-Encoder Rerank &middot; Corrective RAG &middot; Citation-Grounded Generation &middot; OpenTelemetry
        </span>
      </footer>
    </div>
  );
}

export default App;
