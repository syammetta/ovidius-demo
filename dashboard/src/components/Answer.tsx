import type { Citation } from "../types";

interface Props {
  text: string;
  isStreaming: boolean;
  citations: Citation[];
  confidence: string | null;
}

export default function Answer({ text, isStreaming, citations, confidence }: Props) {
  if (!text && !isStreaming) {
    return (
      <div className="bg-[var(--bg-card)] border border-[var(--border)] rounded-lg p-6 flex items-center justify-center min-h-[200px]">
        <p className="text-sm text-[var(--text-muted)]">Response will appear here</p>
      </div>
    );
  }

  return (
    <div className="bg-[var(--bg-card)] border border-[var(--border)] rounded-lg p-4 space-y-3">
      <h3 className="text-xs font-medium text-[var(--text-secondary)] uppercase tracking-wider">Response</h3>

      {/* Answer text */}
      <div className="text-sm leading-relaxed text-[var(--text-primary)] whitespace-pre-wrap">
        <span>{text}</span>
        {isStreaming && <span className="streaming-cursor" />}
      </div>

      {/* Citations */}
      {citations.length > 0 && (
        <div className="border-t border-[var(--border)] pt-3 space-y-1.5">
          <h4 className="text-[11px] text-[var(--text-muted)] uppercase tracking-wider">Sources</h4>
          {citations.map((c) => (
            <a
              key={c.index}
              href={c.source_url}
              target="_blank"
              rel="noopener noreferrer"
              className="flex items-start gap-2 px-2 py-1.5 rounded-md hover:bg-[var(--bg-card-hover)] transition-colors group"
            >
              <span className="text-[11px] text-[var(--accent)] font-mono font-medium min-w-[20px]">
                [{c.index}]
              </span>
              <div className="flex-1 min-w-0">
                <span className="text-xs text-[var(--text-primary)] group-hover:text-[var(--accent)] transition-colors">
                  {c.source_title}
                </span>
                <span className="block text-[10px] text-[var(--text-muted)] truncate">{c.source_url}</span>
              </div>
              <svg className="w-3 h-3 text-[var(--text-muted)] mt-0.5 opacity-0 group-hover:opacity-100 transition-opacity" fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" d="M13.5 6H5.25A2.25 2.25 0 003 8.25v10.5A2.25 2.25 0 005.25 21h10.5A2.25 2.25 0 0018 18.75V10.5m-10.5 6L21 3m0 0h-5.25M21 3v5.25" />
              </svg>
            </a>
          ))}
        </div>
      )}
    </div>
  );
}
