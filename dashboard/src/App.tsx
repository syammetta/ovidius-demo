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
    // Reset state
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

    send(question, mode);
  }

  return (
    <div className="min-h-screen flex flex-col">
      {/* Header */}
      <header className="border-b border-[var(--border)] px-6 py-3 flex items-center justify-between">
        <div className="flex items-center gap-3">
          <div className="w-7 h-7 rounded-lg bg-[var(--accent)] flex items-center justify-center">
            <svg className="w-4 h-4 text-white" fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" d="M12 6.042A8.967 8.967 0 006 3.75c-1.052 0-2.062.18-3 .512v14.25A8.987 8.987 0 016 18c2.305 0 4.408.867 6 2.292m0-14.25a8.966 8.966 0 016-2.292c1.052 0 2.062.18 3 .512v14.25A8.987 8.987 0 0018 18a8.967 8.967 0 00-6 2.292m0-14.25v14.25" />
            </svg>
          </div>
          <div>
            <h1 className="text-sm font-semibold text-[var(--text-primary)]">Ovidius</h1>
            <p className="text-[10px] text-[var(--text-muted)]">IRS Tax Documentation QA</p>
          </div>
        </div>
        <div className="flex items-center gap-4">
          {health && (
            <span className="text-[10px] text-[var(--text-muted)]">
              {health.child_chunks.toLocaleString()} chunks · {health.parent_chunks.toLocaleString()} parents
            </span>
          )}
          <div className="flex items-center gap-1.5">
            <div className={`w-1.5 h-1.5 rounded-full ${
              status === "connected" ? "bg-[var(--green)]" : "bg-[var(--text-muted)]"
            }`} />
            <span className="text-[10px] text-[var(--text-muted)]">{status}</span>
          </div>
        </div>
      </header>

      {/* Main content */}
      <main className="flex-1 p-6 space-y-4 max-w-[1400px] mx-auto w-full">
        {/* Query input */}
        <QueryInput
          onSubmit={handleSubmit}
          isProcessing={isProcessing}
          connectionStatus={status}
          onConnect={connect}
        />

        {/* Pipeline + Answer side by side */}
        <div className="grid grid-cols-1 lg:grid-cols-[320px_1fr] gap-4">
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
          <Answer
            text={answer}
            isStreaming={isStreaming}
            citations={citations}
            confidence={confidence}
          />
        </div>

        {/* Trace waterfall */}
        <SpanWaterfall trace={trace} />

        {/* Query history */}
        <History key={historyKey} />
      </main>

      {/* Footer */}
      <footer className="border-t border-[var(--border)] px-6 py-2 text-center">
        <span className="text-[10px] text-[var(--text-muted)]">
          Hybrid Search + Cross-Encoder Rerank + Corrective RAG + Citation-Grounded Generation · OpenTelemetry Instrumented
        </span>
      </footer>
    </div>
  );
}

export default App;
