import { useCallback, useEffect, useRef, useState } from "react";
import { useQASocket, fetchHealth } from "../api";
import type { WSEvent, RetrievalDetail } from "../types";
import type { MessageData } from "../components/ChatMessage";
import ChatMessage from "../components/ChatMessage";
import QueryInput from "../components/QueryInput";
import Pipeline from "../components/Pipeline";
import SpanWaterfall from "../components/SpanWaterfall";
import RetrievalInsight from "../components/RetrievalInsight";
import History from "../components/History";

const DEFAULT_STAGES = [
  { name: "classify", label: "Query Classification", status: "pending" as const },
  { name: "hybrid_search", label: "Hybrid Search", status: "pending" as const },
  { name: "rerank", label: "Cross-Encoder Rerank", status: "pending" as const },
  { name: "corrective_eval", label: "Corrective RAG Eval", status: "pending" as const },
  { name: "query_retry", label: "Query Transform & Retry", status: "pending" as const },
  { name: "parent_fetch", label: "Parent Chunk Expansion", status: "pending" as const },
  { name: "generation", label: "Answer Generation", status: "pending" as const },
];

let msgCounter = 0;
const SESSION_STORAGE_KEY = "ovidius_ws_session_id";
function nextId() {
  return `msg-${++msgCounter}`;
}

export default function AskPage() {
  const [messages, setMessages] = useState<MessageData[]>([]);
  const [isProcessing, setIsProcessing] = useState(false);
  const [historyKey, setHistoryKey] = useState(0);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [health, setHealth] = useState<{ child_chunks: number; parent_chunks: number } | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const answerRef = useRef("");
  const assistantIdRef = useRef("");

  useEffect(() => {
    fetchHealth().then(setHealth);
  }, []);

  useEffect(() => {
    const existingSession = window.localStorage.getItem(SESSION_STORAGE_KEY);
    if (existingSession) setSessionId(existingSession);
  }, []);

  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [messages]);

  const updateAssistant = useCallback((updater: (prev: MessageData) => MessageData) => {
    setMessages((prev) => {
      const id = assistantIdRef.current;
      return prev.map((m) => (m.id === id ? updater(m) : m));
    });
  }, []);

  const handleEvent = useCallback((event: WSEvent) => {
    switch (event.type) {
      case "start":
        updateAssistant((m) => ({ ...m, traceId: event.trace_id }));
        if (event.session_id) {
          setSessionId(event.session_id);
          window.localStorage.setItem(SESSION_STORAGE_KEY, event.session_id);
        }
        break;

      case "stage": {
        const stageName = event.stage;
        const status = event.status as "running" | "complete";
        const { type: _t, stage: _s, status: _st, ...stageDetail } = event;
        updateAssistant((m) => ({
          ...m,
          stages: (m.stages || []).map((s) =>
            s.name === stageName
              ? { ...s, status, duration_ms: typeof event.duration_ms === "number" ? event.duration_ms : s.duration_ms, detail: { ...s.detail, ...stageDetail } }
              : s,
          ),
        }));
        break;
      }

      case "retrieval_complete": {
        const { type: _t, ...rd } = event;
        updateAssistant((m) => ({
          ...m,
          confidence: event.confidence,
          retrievalDetail: rd as RetrievalDetail,
        }));
        break;
      }

      case "tool_call":
        updateAssistant((m) => ({
          ...m,
          toolCalls: [...(m.toolCalls || []), { tool_name: event.tool_name, tool_input: event.tool_input, duration_ms: 0 }],
        }));
        break;

      case "tool_result":
        updateAssistant((m) => {
          const calls = [...(m.toolCalls || [])];
          const last = calls.findLast((tc) => tc.tool_name === event.tool_name);
          if (last) {
            last.duration_ms = event.duration_ms;
            last.result_preview = event.result_preview;
          }
          return { ...m, toolCalls: calls };
        });
        break;

      case "text_delta":
        answerRef.current += event.text;
        updateAssistant((m) => ({ ...m, text: answerRef.current }));
        break;

      case "done": {
        const d = event as Record<string, unknown>;
        updateAssistant((m) => ({
          ...m,
          isStreaming: false,
          citations: (d.citations as MessageData["citations"]) || m.citations,
          confidence: (d.confidence as string) || m.confidence,
          totalMs: typeof d.total_ms === "number" ? d.total_ms : m.totalMs,
          retrievalMs: typeof d.retrieval_ms === "number" ? d.retrieval_ms : m.retrievalMs,
          generationMs: typeof d.generation_ms === "number" ? d.generation_ms : m.generationMs,
          chunksUsed: typeof d.chunks_used === "number" ? d.chunks_used : m.chunksUsed,
          trace: (d.trace as MessageData["trace"]) || m.trace,
        }));
        if (typeof d.session_id === "string") {
          setSessionId(d.session_id);
          window.localStorage.setItem(SESSION_STORAGE_KEY, d.session_id);
        }
        setHistoryKey((k) => k + 1);
        setIsProcessing(false);
        break;
      }

      case "error":
        updateAssistant((m) => ({ ...m, text: `Error: ${event.message}`, isStreaming: false }));
        setIsProcessing(false);
        break;
    }
  }, [updateAssistant]);

  const { status, connect, send } = useQASocket(handleEvent);

  useEffect(() => {
    connect();
  }, [connect]);

  function handleSubmit(question: string, mode: "direct" | "agent") {
    const userId = nextId();
    const assistantId = nextId();
    assistantIdRef.current = assistantId;
    answerRef.current = "";

    const userMsg: MessageData = { id: userId, role: "user", text: question };
    const assistantMsg: MessageData = {
      id: assistantId,
      role: "assistant",
      text: "",
      stages: DEFAULT_STAGES.map((s) => ({ ...s })),
      toolCalls: [],
      citations: [],
      isStreaming: true,
    };

    setMessages((prev) => [...prev, userMsg, assistantMsg]);
    setIsProcessing(true);
    send(question, mode, sessionId);
  }

  const hasMessages = messages.length > 0;
  const latestAssistant = [...messages].reverse().find((m) => m.role === "assistant");

  return (
    <div className="flex flex-col h-full">
      {/* Stats bar */}
      <div className="flex items-center justify-between px-6 py-2 border-b border-[var(--border-light)]">
        <div className="flex items-center gap-4">
          {health && (
            <span className="text-xs text-[var(--text-muted)]">
              {health.child_chunks.toLocaleString()} chunks &middot; {health.parent_chunks.toLocaleString()} parents
            </span>
          )}
        </div>
        <div className="flex items-center gap-1.5">
          <div className={`w-2 h-2 rounded-full ${
            status === "connected" ? "bg-[var(--green)]" : "bg-[var(--text-muted)]"
          }`} />
          <span className="text-xs text-[var(--text-muted)] capitalize">{status}</span>
        </div>
      </div>

      {/* Messages + insights layout */}
      <div className="flex-1 min-h-0 px-6 py-6">
        <div className="max-w-[1400px] mx-auto h-full grid grid-cols-1 lg:grid-cols-[minmax(0,1fr)_360px] gap-5">
          <section ref={scrollRef} className="min-h-0 overflow-y-auto">
            <div className="max-w-3xl mx-auto space-y-5 pr-1">
              {!hasMessages && !isProcessing && (
                <div className="flex items-center justify-center py-24">
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

              {messages.map((msg) => (
                <ChatMessage key={msg.id} msg={msg} />
              ))}
            </div>
          </section>

          <aside className="min-h-0 overflow-y-auto space-y-4">
            <div className="bg-[var(--surface)] rounded-xl p-4 space-y-2" style={{ boxShadow: "var(--shadow-sm)" }}>
              <h3 className="text-sm font-medium text-[var(--text)]">Session</h3>
              <div className="text-xs text-[var(--text-secondary)] space-y-1">
                <div className="font-mono break-all">{sessionId ?? "Creating..."}</div>
                <div>
                  Confidence: <span className="font-medium">{latestAssistant?.confidence ? latestAssistant.confidence.replace("_", " ") : "—"}</span>
                </div>
                <div>
                  Latency: <span className="font-mono">{latestAssistant?.totalMs !== undefined ? `${latestAssistant.totalMs.toFixed(0)}ms` : "—"}</span>
                </div>
              </div>
            </div>

            {latestAssistant && (
              <div className="bg-[var(--surface)] rounded-xl p-4" style={{ boxShadow: "var(--shadow-sm)" }}>
                <Pipeline
                  stages={latestAssistant.stages || []}
                  toolCalls={latestAssistant.toolCalls || []}
                  confidence={latestAssistant.confidence || null}
                  totalMs={latestAssistant.totalMs ?? null}
                  traceId={latestAssistant.traceId ?? null}
                  retrievalMs={latestAssistant.retrievalMs ?? null}
                  generationMs={latestAssistant.generationMs ?? null}
                  chunksUsed={latestAssistant.chunksUsed ?? null}
                />
              </div>
            )}

            {latestAssistant && (
              <RetrievalInsight
                detail={latestAssistant.retrievalDetail}
                stages={latestAssistant.stages || []}
              />
            )}

            <SpanWaterfall trace={latestAssistant?.trace || null} />
            <History key={historyKey} sessionId={sessionId} title="Recent Searches" />
          </aside>
        </div>
      </div>

      {/* Input */}
      <div className="border-t border-[var(--border-light)] bg-[var(--bg-secondary)] px-6 py-4">
        <div className="max-w-[1400px] mx-auto lg:pr-[380px]">
          <QueryInput
            onSubmit={handleSubmit}
            isProcessing={isProcessing}
            connectionStatus={status}
            onConnect={connect}
            hideExamples={hasMessages || isProcessing}
          />
        </div>
      </div>
    </div>
  );
}
