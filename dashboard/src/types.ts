export type StageStatus = "pending" | "running" | "complete" | "error";

export interface PipelineStage {
  name: string;
  label: string;
  status: StageStatus;
  duration_ms?: number;
  detail?: Record<string, unknown>;
}

export interface Citation {
  index: number;
  source_url: string;
  source_title: string;
}

export interface SourceInfo {
  title: string;
  url: string;
  type: string;
  method: string;
  parent_id: string;
}

export interface ToolCall {
  tool_name: string;
  tool_input?: Record<string, unknown>;
  result_preview?: string;
  duration_ms: number;
}

export interface Span {
  span_id: string;
  parent_span_id: string | null;
  name: string;
  start_ns: number;
  end_ns: number;
  duration_ms: number;
  attributes: Record<string, unknown>;
  status: string;
  status_description: string | null;
  events: unknown[];
}

export interface TraceData {
  trace_id: string;
  root_name: string | null;
  span_count: number;
  duration_ms: number | null;
  status: string | null;
  spans: Span[];
}

export interface QueryResult {
  answer: string;
  citations: Citation[];
  confidence: string;
  retrieval_method?: string;
  chunks_used?: number;
  parent_chunks_used?: number;
  trace_id: string | null;
  total_ms: number;
  retrieval_ms?: number;
  generation_ms?: number;
  trace?: TraceData | null;
  tool_calls?: ToolCall[];
  turn_count?: number;
}

export interface QueryLogEntry {
  id: number;
  question: string;
  answer: string;
  confidence: string | null;
  latency_ms: number | null;
  interface: string;
  trace_id: string | null;
  created_at: string;
}

export type ConnectionStatus = "connecting" | "connected" | "disconnected" | "error";

export type WSEvent =
  | { type: "start"; trace_id: string; mode?: string }
  | { type: "stage"; stage: string; status: string; [key: string]: unknown }
  | { type: "retrieval_complete"; confidence: string; chunks: number; parents: number; filtered: string; duration_ms: number; sources: SourceInfo[] }
  | { type: "tool_call"; tool_name: string; tool_input: Record<string, unknown> }
  | { type: "tool_result"; tool_name: string; result_preview: string; duration_ms: number }
  | { type: "text_delta"; text: string }
  | { type: "done"; [key: string]: unknown }
  | { type: "error"; message: string };
