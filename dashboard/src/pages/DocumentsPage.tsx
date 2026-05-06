import { useEffect, useState } from "react";
import { fetchDocuments, fetchDocumentDetail, deleteDocument } from "../api";
import type { DocumentRow, ParentDetail, ChunkDetail } from "../api";

export default function DocumentsPage() {
  const [docs, setDocs] = useState<DocumentRow[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detail, setDetail] = useState<{ parent: ParentDetail; chunks: ChunkDetail[] } | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(0);
  const pageSize = 100;

  async function load(offset = 0) {
    setLoading(true);
    try {
      const data = await fetchDocuments(pageSize, offset);
      setDocs(data.documents);
      setTotal(data.total);
    } catch {
      // ignore
    }
    setLoading(false);
  }

  useEffect(() => { load(page * pageSize); }, [page]);

  async function openDetail(parentId: string) {
    if (selectedId === parentId) {
      setSelectedId(null);
      setDetail(null);
      return;
    }
    setSelectedId(parentId);
    setDetailLoading(true);
    try {
      const data = await fetchDocumentDetail(parentId);
      setDetail(data);
    } catch {
      setDetail(null);
    }
    setDetailLoading(false);
  }

  async function handleDelete(parentId: string) {
    if (!window.confirm("Delete this document and all its chunks?")) return;
    try {
      await deleteDocument(parentId);
      setDocs((prev) => prev.filter((d) => d.parent_id !== parentId));
      setTotal((t) => t - 1);
      if (selectedId === parentId) {
        setSelectedId(null);
        setDetail(null);
      }
    } catch {
      // ignore
    }
  }

  const filtered = search
    ? docs.filter(
        (d) =>
          d.source_title.toLowerCase().includes(search.toLowerCase()) ||
          d.source_url.toLowerCase().includes(search.toLowerCase()),
      )
    : docs;

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="flex items-center justify-between px-6 py-4 border-b border-[var(--border-light)]">
        <div>
          <h1 className="text-lg font-medium text-[var(--text)]">Documents</h1>
          <p className="text-sm text-[var(--text-muted)]">{total} parent chunks in the knowledge base</p>
        </div>
        <button
          onClick={() => load()}
          disabled={loading}
          className="text-sm text-[var(--accent)] hover:underline disabled:opacity-50"
        >
          Refresh
        </button>
      </div>

      {/* Search */}
      <div className="px-6 py-3 border-b border-[var(--border-light)]">
        <div className="relative max-w-md">
          <svg className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-[var(--text-muted)]" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" d="M21 21l-5.197-5.197m0 0A7.5 7.5 0 105.196 5.196a7.5 7.5 0 0010.607 10.607z" />
          </svg>
          <input
            type="text"
            placeholder="Search documents..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="w-full pl-9 pr-4 py-2 text-sm rounded-lg border border-[var(--border)] bg-[var(--surface)] text-[var(--text)] placeholder-[var(--text-muted)] focus:outline-none focus:border-[var(--accent)]"
          />
        </div>
      </div>

      {/* Document list */}
      <div className="flex-1 overflow-y-auto px-6 py-4 space-y-2">
        {loading && (
          <div className="text-sm text-[var(--text-muted)] text-center py-8">Loading documents...</div>
        )}

        {!loading && filtered.length === 0 && (
          <div className="text-sm text-[var(--text-muted)] text-center py-8">
            {search ? "No documents match your search" : "No documents ingested yet"}
          </div>
        )}

        {!loading && total > pageSize && (
          <div className="flex items-center justify-between text-sm text-[var(--text-muted)]">
            <span>
              Showing {page * pageSize + 1}–{Math.min((page + 1) * pageSize, total)} of {total}
            </span>
            <div className="flex gap-2">
              <button
                disabled={page === 0}
                onClick={() => setPage((p) => p - 1)}
                className="px-3 py-1 rounded-lg border border-[var(--border)] text-xs hover:bg-[var(--bg-secondary)] disabled:opacity-30"
              >
                Prev
              </button>
              <button
                disabled={(page + 1) * pageSize >= total}
                onClick={() => setPage((p) => p + 1)}
                className="px-3 py-1 rounded-lg border border-[var(--border)] text-xs hover:bg-[var(--bg-secondary)] disabled:opacity-30"
              >
                Next
              </button>
            </div>
          </div>
        )}

        {filtered.map((doc) => (
          <div key={doc.parent_id}>
            {/* Document row */}
            <div
              className={`rounded-xl border transition-colors cursor-pointer ${
                selectedId === doc.parent_id
                  ? "border-[var(--accent)] bg-[var(--accent-light)]"
                  : "border-[var(--border-light)] bg-[var(--surface)] hover:border-[var(--border)]"
              }`}
              style={selectedId !== doc.parent_id ? { boxShadow: "var(--shadow-sm)" } : undefined}
            >
              <div
                className="flex items-center gap-4 px-4 py-3"
                onClick={() => openDetail(doc.parent_id)}
              >
                {/* Doc type badge */}
                <span className={`text-[10px] px-2 py-0.5 rounded-full font-medium shrink-0 ${
                  doc.document_type === "narrative"
                    ? "bg-[var(--blue-light)] text-[var(--blue)]"
                    : doc.document_type === "api_reference"
                      ? "bg-[var(--purple-light)] text-[var(--purple)]"
                      : doc.document_type === "code_heavy"
                        ? "bg-[var(--yellow-light)] text-[var(--yellow)]"
                        : "bg-[var(--bg-tertiary)] text-[var(--text-secondary)]"
                }`}>
                  {doc.document_type}
                </span>

                {/* Title + URL */}
                <div className="flex-1 min-w-0">
                  <p className="text-sm font-medium text-[var(--text)] truncate">{doc.source_title}</p>
                  <p className="text-xs text-[var(--text-muted)] truncate">{doc.source_url}</p>
                </div>

                {/* Stats */}
                <div className="flex items-center gap-4 shrink-0 text-xs text-[var(--text-muted)]">
                  <span>{doc.child_count} chunks</span>
                  <span>{doc.token_count.toLocaleString()} tokens</span>
                </div>

                {/* Expand icon */}
                <svg
                  className={`w-4 h-4 text-[var(--text-muted)] transition-transform ${
                    selectedId === doc.parent_id ? "rotate-180" : ""
                  }`}
                  fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor"
                >
                  <path strokeLinecap="round" strokeLinejoin="round" d="M19.5 8.25l-7.5 7.5-7.5-7.5" />
                </svg>
              </div>

              {/* Expanded detail */}
              {selectedId === doc.parent_id && (
                <div className="px-4 pb-4 border-t border-[var(--border-light)]">
                  {detailLoading ? (
                    <p className="text-sm text-[var(--text-muted)] py-4">Loading chunks...</p>
                  ) : detail ? (
                    <div className="space-y-3 pt-3">
                      {/* Parent content preview */}
                      <div>
                        <h4 className="text-xs font-medium text-[var(--text-secondary)] mb-1">Parent Content</h4>
                        <p className="text-xs text-[var(--text-secondary)] bg-[var(--bg-secondary)] rounded-lg p-3 max-h-32 overflow-y-auto whitespace-pre-wrap leading-relaxed">
                          {detail.parent.content.slice(0, 800)}{detail.parent.content.length > 800 ? "..." : ""}
                        </p>
                      </div>

                      {/* Child chunks */}
                      <div>
                        <h4 className="text-xs font-medium text-[var(--text-secondary)] mb-2">
                          Child Chunks ({detail.chunks.length})
                        </h4>
                        <div className="space-y-2 max-h-64 overflow-y-auto">
                          {detail.chunks.map((chunk) => (
                            <div
                              key={chunk.chunk_id}
                              className="bg-[var(--bg-secondary)] rounded-lg p-3"
                            >
                              <div className="flex items-center justify-between mb-1">
                                <span className="text-[10px] font-mono text-[var(--text-muted)]">{chunk.chunk_id}</span>
                                <span className="text-[10px] text-[var(--text-muted)]">{chunk.token_count} tokens</span>
                              </div>
                              <p className="text-xs text-[var(--text-secondary)] whitespace-pre-wrap leading-relaxed">
                                {chunk.content.slice(0, 300)}{chunk.content.length > 300 ? "..." : ""}
                              </p>
                              {chunk.contextual_content && (
                                <p className="text-[10px] text-[var(--accent)] mt-1 italic">
                                  +context: {chunk.contextual_content.slice(0, 120)}...
                                </p>
                              )}
                            </div>
                          ))}
                        </div>
                      </div>

                      {/* Actions */}
                      <div className="flex items-center gap-3 pt-2">
                        <a
                          href={doc.source_url}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="text-xs text-[var(--accent)] hover:underline"
                        >
                          View source
                        </a>
                        <button
                          onClick={() => handleDelete(doc.parent_id)}
                          className="text-xs text-[var(--red)] hover:underline"
                        >
                          Delete
                        </button>
                      </div>
                    </div>
                  ) : (
                    <p className="text-sm text-[var(--red)] py-4">Failed to load details</p>
                  )}
                </div>
              )}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
