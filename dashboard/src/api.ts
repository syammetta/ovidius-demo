import { useCallback, useEffect, useRef, useState } from "react";
import type { ConnectionStatus, WSEvent, QueryLogEntry } from "./types";

function wsUrl(): string {
  const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${proto}//${window.location.host}/ws/qa`;
}

export function useQASocket(onEvent: (event: WSEvent) => void) {
  const wsRef = useRef<WebSocket | null>(null);
  const [status, setStatus] = useState<ConnectionStatus>("disconnected");
  const onEventRef = useRef(onEvent);
  onEventRef.current = onEvent;

  const connect = useCallback(() => {
    if (wsRef.current?.readyState === WebSocket.OPEN) return;

    setStatus("connecting");
    const ws = new WebSocket(wsUrl());
    wsRef.current = ws;

    ws.onopen = () => setStatus("connected");

    ws.onmessage = (e) => {
      try {
        const data = JSON.parse(e.data) as WSEvent;
        onEventRef.current(data);
      } catch {
        // ignore malformed messages
      }
    };

    ws.onerror = () => setStatus("error");

    ws.onclose = () => {
      setStatus("disconnected");
      wsRef.current = null;
    };
  }, []);

  const disconnect = useCallback(() => {
    wsRef.current?.close();
    wsRef.current = null;
    setStatus("disconnected");
  }, []);

  const send = useCallback(
    (question: string, mode: "direct" | "agent" = "direct", sessionId?: string | null) => {
      if (wsRef.current?.readyState !== WebSocket.OPEN) return;
      wsRef.current.send(JSON.stringify({
        question,
        mode,
        ...(sessionId ? { session_id: sessionId } : {}),
      }));
    },
    [],
  );

  useEffect(() => {
    return () => {
      wsRef.current?.close();
    };
  }, []);

  return { status, connect, disconnect, send };
}

export async function fetchQueryLogs(limit = 20, sessionId?: string | null): Promise<QueryLogEntry[]> {
  const params = new URLSearchParams({ limit: String(limit) });
  if (sessionId) params.set("session_id", sessionId);
  const res = await fetch(`/query-logs?${params.toString()}`);
  if (!res.ok) return [];
  return res.json();
}

export async function fetchHealth(): Promise<{
  status: string;
  child_chunks: number;
  parent_chunks: number;
} | null> {
  try {
    const res = await fetch("/health");
    if (!res.ok) return null;
    return res.json();
  } catch {
    return null;
  }
}

export async function fetchDocuments(limit = 100, offset = 0) {
  const res = await fetch(`/api/documents?limit=${limit}&offset=${offset}`);
  if (!res.ok) throw new Error("Failed to fetch documents");
  return res.json() as Promise<{ documents: DocumentRow[]; total: number }>;
}

export async function fetchDocumentDetail(parentId: string) {
  const res = await fetch(`/api/documents/${encodeURIComponent(parentId)}`);
  if (!res.ok) throw new Error("Failed to fetch document");
  return res.json() as Promise<{ parent: ParentDetail; chunks: ChunkDetail[] }>;
}

export async function deleteDocument(parentId: string) {
  const res = await fetch(`/api/documents/${encodeURIComponent(parentId)}`, { method: "DELETE" });
  if (!res.ok) throw new Error("Failed to delete document");
  return res.json();
}

export async function ingestUrl(url: string, useCache = true) {
  const res = await fetch("/api/ingest/url", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ url, use_cache: useCache }),
  });
  if (!res.ok) throw new Error("Failed to start ingestion");
  return res.json() as Promise<{ task_id: string; status: string }>;
}

export async function fetchIngestTasks() {
  const res = await fetch("/api/ingest/tasks");
  if (!res.ok) return [];
  return res.json() as Promise<IngestTask[]>;
}

export async function fetchIngestTask(taskId: string) {
  const res = await fetch(`/api/ingest/tasks/${taskId}`);
  if (!res.ok) return null;
  return res.json() as Promise<IngestTask>;
}

export async function ingestCorpus() {
  const res = await fetch("/api/ingest/corpus", { method: "POST" });
  if (!res.ok) throw new Error("Failed to start corpus ingestion");
  return res.json() as Promise<{ task_id: string; status: string }>;
}

export async function pauseIngestTask(taskId: string) {
  const res = await fetch(`/api/ingest/tasks/${encodeURIComponent(taskId)}/pause`, { method: "POST" });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: "Failed to pause task" }));
    throw new Error(err.detail || "Failed to pause task");
  }
  return res.json() as Promise<IngestTask>;
}

export async function resumeIngestTask(taskId: string) {
  const res = await fetch(`/api/ingest/tasks/${encodeURIComponent(taskId)}/resume`, { method: "POST" });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: "Failed to resume task" }));
    throw new Error(err.detail || "Failed to resume task");
  }
  return res.json() as Promise<IngestTask>;
}

export async function ingestFile(file: File) {
  const form = new FormData();
  form.append("file", file);
  const res = await fetch("/api/ingest/file", { method: "POST", body: form });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: "Upload failed" }));
    throw new Error(err.detail || "Upload failed");
  }
  return res.json() as Promise<{ task_id: string; status: string; filename: string }>;
}

export async function fetchTraces(limit = 50) {
  const res = await fetch(`/traces?limit=${limit}`);
  if (!res.ok) return [];
  return res.json();
}

export async function fetchTrace(traceId: string) {
  const res = await fetch(`/traces/${traceId}`);
  if (!res.ok) return null;
  return res.json();
}

export async function fetchMetrics() {
  const res = await fetch("/metrics");
  if (!res.ok) return null;
  return res.json();
}

export interface DocumentRow {
  parent_id: string;
  source_url: string;
  source_title: string;
  section: string;
  document_type: string;
  token_count: number;
  child_count: number;
  created_at: string;
}

export interface ParentDetail {
  parent_id: string;
  content: string;
  source_url: string;
  source_title: string;
  section: string;
  document_type: string;
  token_count: number;
  created_at: string;
}

export interface ChunkDetail {
  chunk_id: string;
  content: string;
  contextual_content: string | null;
  token_count: number;
  section: string;
}

export interface IngestTask {
  task_id: string;
  status: "queued" | "running" | "paused" | "completed" | "failed";
  url: string;
  stats: { parents: number; children: number; title?: string; document_type?: string } | null;
  progress: {
    phase?: string;
    pipeline_stage?: string;
    pipeline_steps?: {
      classify_metadata?: "pending" | "running" | "complete" | "skipped";
      chunking?: "pending" | "running" | "complete" | "skipped";
      contextualizing?: "pending" | "running" | "complete" | "skipped";
      storing_parents?: "pending" | "running" | "complete" | "skipped";
      embedding_children?: "pending" | "running" | "complete" | "skipped";
    };
    metadata_labels?: {
      doc_type?: string;
      section?: string;
      tax_topics?: string[];
      metadata_tags?: string[];
      llm_used?: boolean;
    };
    completion?: number;
    total_docs?: number;
    current_doc?: number;
    crawled_docs?: number;
    processed_docs?: number;
    failed_crawls?: number;
    current_url?: string;
    current_title?: string;
    corpus_progress?: {
      next_index?: number;
      processed_docs?: number;
      total_docs?: number;
      parents?: number;
      children?: number;
      crawled_docs?: number;
      failed_crawls?: number;
    };
  } | null;
  error: string | null;
  logs: string[];
}

export interface EvalRun {
  run_id: string;
  started_at: string | null;
  finished_at: string | null;
  config: Record<string, unknown> | null;
  metrics: Record<string, unknown> | null;
  pair_count: number | null;
  status: string;
}

export interface EvalResult {
  pair_id: string;
  tier: string;
  question: string;
  expected_answer: string | null;
  actual_answer: string | null;
  contexts: unknown;
  metrics: Record<string, unknown> | null;
  trace_id: string | null;
  created_at: string;
}

export interface EvalSummary {
  recent_runs: EvalRun[];
  tier_breakdown: {
    tier: string;
    count: number;
    avg_faithfulness: number | null;
    avg_relevancy: number | null;
    avg_precision: number | null;
  }[];
}

export async function triggerEvalRun(): Promise<{ run_id: string; status: string }> {
  const res = await fetch("/eval/run", { method: "POST" });
  if (!res.ok) throw new Error("Failed to trigger eval run");
  return res.json();
}

export async function fetchEvalRuns(limit = 20): Promise<EvalRun[]> {
  const res = await fetch(`/eval/runs?limit=${limit}`);
  if (!res.ok) return [];
  return res.json();
}

export async function fetchEvalRun(runId: string): Promise<{ run: EvalRun; results: EvalResult[] } | null> {
  const res = await fetch(`/eval/runs/${encodeURIComponent(runId)}`);
  if (!res.ok) return null;
  return res.json();
}

export async function fetchEvalSummary(): Promise<EvalSummary | null> {
  const res = await fetch("/eval/summary");
  if (!res.ok) return null;
  return res.json();
}

export async function deleteEvalRun(runId: string): Promise<void> {
  const res = await fetch(`/eval/runs/${encodeURIComponent(runId)}`, { method: "DELETE" });
  if (!res.ok) throw new Error("Failed to delete eval run");
}
