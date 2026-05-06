import type { Citation } from "../types";

interface Props {
  text: string;
  isStreaming: boolean;
  citations: Citation[];
  confidence: string | null;
}

export default function Answer({ text, isStreaming, citations }: Props) {
  if (!text && !isStreaming) return null;

  return (
    <div className="space-y-4">
      {/* Answer text */}
      <div className="text-sm leading-7 text-[var(--text)] whitespace-pre-wrap">
        <span>{text}</span>
        {isStreaming && <span className="streaming-cursor" />}
      </div>

      {/* Citations */}
      {citations.length > 0 && (
        <div className="space-y-2 pt-2 border-t border-[var(--border-light)]">
          <h4 className="text-xs font-medium text-[var(--text-secondary)]">Sources</h4>
          <div className="grid gap-2">
            {citations.map((c) => (
              <a
                key={c.index}
                href={c.source_url}
                target="_blank"
                rel="noopener noreferrer"
                className="flex items-start gap-3 px-3 py-2 rounded-xl border border-[var(--border-light)] hover:bg-[var(--bg-secondary)] transition-colors group"
              >
                <span className="text-xs text-[var(--accent)] font-medium mt-0.5 shrink-0">
                  {c.index}
                </span>
                <div className="flex-1 min-w-0">
                  <span className="text-sm text-[var(--text)] group-hover:text-[var(--accent)] transition-colors block">
                    {c.source_title}
                  </span>
                  <span className="text-xs text-[var(--text-muted)] truncate block">{c.source_url}</span>
                </div>
                <svg className="w-3.5 h-3.5 text-[var(--text-muted)] mt-1 opacity-0 group-hover:opacity-100 transition-opacity shrink-0" fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" d="M13.5 6H5.25A2.25 2.25 0 003 8.25v10.5A2.25 2.25 0 005.25 21h10.5A2.25 2.25 0 0018 18.75V10.5m-10.5 6L21 3m0 0h-5.25M21 3v5.25" />
                </svg>
              </a>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
