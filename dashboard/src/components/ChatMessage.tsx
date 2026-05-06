import type { PipelineStage, Citation, ToolCall, TraceData, RetrievalDetail } from "../types";
import Answer from "./Answer";

export interface MessageData {
  id: string;
  role: "user" | "assistant";
  text: string;
  stages?: PipelineStage[];
  toolCalls?: ToolCall[];
  citations?: Citation[];
  confidence?: string;
  totalMs?: number;
  retrievalMs?: number;
  generationMs?: number;
  chunksUsed?: number;
  traceId?: string;
  trace?: TraceData | null;
  isStreaming?: boolean;
  retrievalDetail?: RetrievalDetail;
}

export default function ChatMessage({ msg }: { msg: MessageData }) {
  if (msg.role === "user") {
    return (
      <div className="flex justify-end">
        <div className="bg-[var(--accent)] text-white px-4 py-2.5 rounded-2xl rounded-br-sm max-w-lg text-sm">
          {msg.text}
        </div>
      </div>
    );
  }

  return (
    <div className="flex gap-3 fade-in">
      <div className="w-7 h-7 rounded-full bg-[var(--accent-light)] flex items-center justify-center shrink-0 mt-0.5">
        <svg className="w-4 h-4 text-[var(--accent)]" viewBox="0 0 24 24" fill="currentColor">
          <path d="M9.813 15.904L9 18.75l-.813-2.846a4.5 4.5 0 00-3.09-3.09L2.25 12l2.846-.813a4.5 4.5 0 003.09-3.09L9 5.25l.813 2.846a4.5 4.5 0 003.09 3.09L15.75 12l-2.846.813a4.5 4.5 0 00-3.09 3.09zM18.259 8.715L18 9.75l-.259-1.035a3.375 3.375 0 00-2.455-2.456L14.25 6l1.036-.259a3.375 3.375 0 002.455-2.456L18 2.25l.259 1.035a3.375 3.375 0 002.455 2.456L21.75 6l-1.036.259a3.375 3.375 0 00-2.455 2.456z" />
        </svg>
      </div>
      <div className="flex-1 min-w-0">
        <Answer
          text={msg.text}
          isStreaming={Boolean(msg.isStreaming)}
          citations={msg.citations || []}
          confidence={msg.confidence || null}
        />
      </div>
    </div>
  );
}
