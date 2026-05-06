import { useCallback, useEffect, useRef, useState } from "react";
import {
  ingestUrl,
  ingestFile,
  ingestCorpus,
  fetchIngestTasks,
  fetchIngestTask,
  pauseIngestTask,
  resumeIngestTask,
} from "../api";
import type { IngestTask } from "../api";

const PIPELINE_STEPS = [
  { key: "classify_metadata", label: "Classify Metadata" },
  { key: "chunking", label: "Chunk" },
  { key: "contextualizing", label: "Context" },
  { key: "storing_parents", label: "Store Parents" },
  { key: "embedding_children", label: "Embed Children" },
] as const;

export default function IngestPage() {
  const [url, setUrl] = useState("");
  const [useCache, setUseCache] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [tasks, setTasks] = useState<IngestTask[]>([]);
  const [dragging, setDragging] = useState(false);
  const [uploadError, setUploadError] = useState("");
  const [taskAction, setTaskAction] = useState<Record<string, "pause" | "resume" | undefined>>({});
  const [showFailed, setShowFailed] = useState(false);
  const pollingRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  async function loadTasks() {
    try {
      const data = await fetchIngestTasks();
      setTasks(data);
    } catch {
      // ignore
    }
  }

  useEffect(() => {
    loadTasks();
    return () => {
      if (pollingRef.current) clearInterval(pollingRef.current);
    };
  }, []);

  function startPolling() {
    if (pollingRef.current) return;
    pollingRef.current = setInterval(async () => {
      try {
        const fresh = await fetchIngestTasks();
        setTasks(fresh);
        const stillRunning = fresh.some((t) => t.status === "running" || t.status === "queued");
        if (!stillRunning && pollingRef.current) {
          clearInterval(pollingRef.current);
          pollingRef.current = null;
        }
      } catch {
        // ignore
      }
    }, 2000);
  }

  async function handleUrlSubmit() {
    const u = url.trim();
    if (!u || submitting) return;
    setSubmitting(true);
    setUploadError("");
    try {
      await ingestUrl(u, useCache);
      setUrl("");
      startPolling();
      await loadTasks();
    } catch (e) {
      setUploadError(e instanceof Error ? e.message : "Failed");
    }
    setSubmitting(false);
  }

  const handleFiles = useCallback(async (files: FileList | File[]) => {
    setUploadError("");
    for (const file of Array.from(files)) {
      const ext = file.name.split(".").pop()?.toLowerCase();
      if (!["pdf", "txt", "md", "html", "htm"].includes(ext || "")) {
        setUploadError(`Unsupported file type: .${ext}. Use PDF, TXT, MD, or HTML.`);
        continue;
      }
      try {
        await ingestFile(file);
        startPolling();
        await loadTasks();
      } catch (e) {
        setUploadError(e instanceof Error ? e.message : "Upload failed");
      }
    }
  }, []);

  function handleDragOver(e: React.DragEvent) {
    e.preventDefault();
    setDragging(true);
  }

  function handleDragLeave(e: React.DragEvent) {
    e.preventDefault();
    setDragging(false);
  }

  function handleDrop(e: React.DragEvent) {
    e.preventDefault();
    setDragging(false);
    if (e.dataTransfer.files.length > 0) {
      handleFiles(e.dataTransfer.files);
    }
  }

  async function refreshTask(taskId: string) {
    try {
      const updated = await fetchIngestTask(taskId);
      if (updated) {
        setTasks((prev) => prev.map((t) => (t.task_id === taskId ? updated : t)));
      }
    } catch {
      // ignore
    }
  }

  const statusColor = (s: string) =>
    s === "completed"
      ? "bg-[var(--green-light)] text-[var(--green)]"
      : s === "paused"
        ? "bg-[var(--yellow-light)] text-[var(--yellow)]"
      : s === "failed"
        ? "bg-[var(--red-light)] text-[var(--red)]"
        : "bg-[var(--accent-light)] text-[var(--accent)]";

  const getTaskProgress = (task: IngestTask) => {
    const p = task.progress || {};
    const cp = p.corpus_progress || {};
    const total = p.total_docs ?? cp.total_docs ?? 0;
    const crawled = p.crawled_docs ?? cp.crawled_docs ?? 0;
    const processed = p.processed_docs ?? cp.processed_docs ?? 0;
    const completion = Math.max(0, Math.min(100, p.completion ?? (task.status === "completed" ? 100 : 0)));
    return {
      phase: p.phase || "pending",
      completion,
      total,
      crawled,
      processed,
      failed: p.failed_crawls ?? cp.failed_crawls ?? 0,
      currentTitle: p.current_title || "",
      currentUrl: p.current_url || "",
      currentDoc: p.current_doc ?? cp.next_index ?? 0,
      pipelineStage: p.pipeline_stage || "",
      pipelineSteps: p.pipeline_steps || {},
      metadata: p.metadata_labels || null,
      pauseRequested: !!p.pause_requested,
    };
  };

  const stepClass = (status: string) =>
    status === "complete"
      ? "bg-[var(--green-light)] text-[var(--green)] border-[var(--green)]/20"
      : status === "running"
        ? "bg-[var(--accent-light)] text-[var(--accent)] border-[var(--accent)]/25"
      : status === "skipped"
        ? "bg-[var(--bg-tertiary)] text-[var(--text-muted)] border-[var(--border)]"
        : "bg-[var(--surface)] text-[var(--text-muted)] border-[var(--border-light)]";

  const activeTasks = tasks.filter((t) => t.status !== "failed");
  const failedTasks = tasks.filter((t) => t.status === "failed");

  async function handlePauseTask(taskId: string) {
    setTaskAction((prev) => ({ ...prev, [taskId]: "pause" }));
    setUploadError("");
    try {
      const updated = await pauseIngestTask(taskId);
      setTasks((prev) => prev.map((t) => (t.task_id === taskId ? updated : t)));
    } catch (e) {
      setUploadError(e instanceof Error ? e.message : "Failed to pause task");
    } finally {
      setTaskAction((prev) => ({ ...prev, [taskId]: undefined }));
    }
  }

  async function handleResumeTask(taskId: string) {
    setTaskAction((prev) => ({ ...prev, [taskId]: "resume" }));
    setUploadError("");
    try {
      const updated = await resumeIngestTask(taskId);
      setTasks((prev) => prev.map((t) => (t.task_id === taskId ? updated : t)));
      startPolling();
    } catch (e) {
      setUploadError(e instanceof Error ? e.message : "Failed to resume task");
    } finally {
      setTaskAction((prev) => ({ ...prev, [taskId]: undefined }));
    }
  }

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="px-6 py-4 border-b border-[var(--border-light)]">
        <h1 className="text-lg font-medium text-[var(--text)]">Ingest Documents</h1>
        <p className="text-sm text-[var(--text-muted)]">Add documents by URL or drag and drop files</p>
      </div>

      <div className="flex-1 overflow-y-auto px-6 py-6">
        <div className="max-w-2xl space-y-6">
          {/* Drag & drop zone */}
          <div
            onDragOver={handleDragOver}
            onDragLeave={handleDragLeave}
            onDrop={handleDrop}
            onClick={() => fileInputRef.current?.click()}
            className={`rounded-xl border-2 border-dashed p-8 text-center cursor-pointer transition-colors ${
              dragging
                ? "border-[var(--accent)] bg-[var(--accent-light)]"
                : "border-[var(--border)] bg-[var(--surface)] hover:border-[var(--accent)] hover:bg-[var(--bg-secondary)]"
            }`}
          >
            <input
              ref={fileInputRef}
              type="file"
              multiple
              accept=".pdf,.txt,.md,.html,.htm"
              className="hidden"
              onChange={(e) => e.target.files && handleFiles(e.target.files)}
            />
            <svg className={`w-10 h-10 mx-auto mb-3 ${dragging ? "text-[var(--accent)]" : "text-[var(--text-muted)]"}`} fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" d="M12 16.5V9.75m0 0l3 3m-3-3l-3 3M6.75 19.5a4.5 4.5 0 01-1.41-8.775 5.25 5.25 0 0110.233-2.33 3 3 0 013.758 3.848A3.752 3.752 0 0118 19.5H6.75z" />
            </svg>
            <p className="text-sm font-medium text-[var(--text)]">
              {dragging ? "Drop files here" : "Drag & drop files here"}
            </p>
            <p className="text-xs text-[var(--text-muted)] mt-1">
              PDF, TXT, Markdown, HTML — or click to browse
            </p>
          </div>

          {uploadError && (
            <p className="text-sm text-[var(--red)]">{uploadError}</p>
          )}

          {/* URL input card */}
          <div
            className="bg-[var(--surface)] rounded-xl p-5 space-y-4"
            style={{ boxShadow: "var(--shadow-sm)" }}
          >
            <div>
              <label className="text-sm font-medium text-[var(--text)] block mb-1.5">Or ingest by URL</label>
              <input
                type="url"
                value={url}
                onChange={(e) => setUrl(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && handleUrlSubmit()}
                placeholder="https://www.irs.gov/publications/p17"
                className="w-full px-4 py-2.5 text-sm rounded-lg border border-[var(--border)] bg-[var(--surface)] text-[var(--text)] placeholder-[var(--text-muted)] focus:outline-none focus:border-[var(--accent)]"
              />
            </div>

            <div className="flex items-center justify-between">
              <label className="flex items-center gap-2 text-sm text-[var(--text-secondary)] cursor-pointer">
                <input
                  type="checkbox"
                  checked={useCache}
                  onChange={(e) => setUseCache(e.target.checked)}
                  className="rounded"
                />
                Use R2 cache
              </label>

              <button
                onClick={handleUrlSubmit}
                disabled={submitting || !url.trim()}
                className="px-5 py-2 bg-[var(--accent)] text-white text-sm font-medium rounded-lg hover:opacity-90 transition-opacity disabled:opacity-30"
              >
                {submitting ? "Starting..." : "Ingest"}
              </button>
            </div>

            <p className="text-xs text-[var(--text-muted)]">
              Pipeline: Crawl / Parse → Adaptive Chunk → Contextualize (Claude) → Embed (Voyage) → Store (pgvector)
            </p>
          </div>

          {/* Bulk corpus ingest */}
          <div
            className="bg-[var(--surface)] rounded-xl p-5 space-y-3 border-2 border-dashed border-[var(--accent)]"
            style={{ boxShadow: "var(--shadow-sm)" }}
          >
            <div className="flex items-center justify-between">
              <div>
                <h3 className="text-sm font-medium text-[var(--text)]">Bulk Ingest — Full IRS Corpus</h3>
                <p className="text-xs text-[var(--text-muted)] mt-1">
                  20 publications + 32 tax topics + 5 form instructions = 57 documents
                </p>
              </div>
              <button
                onClick={async () => {
                  setUploadError("");
                  try {
                    await ingestCorpus();
                    startPolling();
                    await loadTasks();
                  } catch (e) {
                    setUploadError(e instanceof Error ? e.message : "Failed");
                  }
                }}
                className="px-5 py-2 bg-[var(--accent)] text-white text-sm font-medium rounded-lg hover:opacity-90 transition-opacity shrink-0"
              >
                Ingest Full Corpus
              </button>
            </div>
            <p className="text-xs text-[var(--text-muted)]">
              Pipeline per doc: Crawl (R2 cached) → Detect Type → Adaptive Chunk (narrative / table / code) → Contextualize (Claude Haiku + prompt caching) → Embed (Voyage-3 1024d) → Store (pgvector + BM25 index)
            </p>
          </div>

          {/* Quick add IRS topics */}
          <div
            className="bg-[var(--surface)] rounded-xl p-5 space-y-3"
            style={{ boxShadow: "var(--shadow-sm)" }}
          >
            <h3 className="text-sm font-medium text-[var(--text)]">Quick Add — IRS Tax Topics</h3>
            <div className="flex flex-wrap gap-2">
              {[
                { label: "Filing Info (P501)", url: "https://www.irs.gov/publications/p501" },
                { label: "Medical Expenses (P502)", url: "https://www.irs.gov/publications/p502" },
                { label: "Investment Income (P550)", url: "https://www.irs.gov/publications/p550" },
                { label: "IRA Contributions (P590a)", url: "https://www.irs.gov/publications/p590a" },
                { label: "Earned Income Credit (P596)", url: "https://www.irs.gov/publications/p596" },
                { label: "Home Mortgage (P936)", url: "https://www.irs.gov/publications/p936" },
                { label: "Education Benefits (P970)", url: "https://www.irs.gov/publications/p970" },
                { label: "HSA (P969)", url: "https://www.irs.gov/publications/p969" },
                { label: "Standard Deduction (TC551)", url: "https://www.irs.gov/taxtopics/tc551" },
                { label: "Capital Gains (TC409)", url: "https://www.irs.gov/taxtopics/tc409" },
              ].map((item) => (
                <button
                  key={item.url}
                  onClick={() => setUrl(item.url)}
                  className="text-xs px-3 py-1.5 rounded-full border border-[var(--border)] text-[var(--text-secondary)] hover:bg-[var(--bg-secondary)] transition-colors"
                >
                  {item.label}
                </button>
              ))}
            </div>
          </div>

          {/* Task history */}
          {tasks.length > 0 && (
            <div
              className="bg-[var(--surface)] rounded-xl p-5 space-y-3"
              style={{ boxShadow: "var(--shadow-sm)" }}
            >
              <div className="flex items-center justify-between">
                <h3 className="text-sm font-medium text-[var(--text)]">Ingestion Tasks</h3>
                <button
                  onClick={loadTasks}
                  className="text-xs text-[var(--accent)] hover:underline"
                >
                  Refresh
                </button>
              </div>

              <div className="space-y-3">
                {activeTasks.map((task) => (
                  <div
                    key={task.task_id}
                    className="border border-[var(--border-light)] rounded-lg p-3 space-y-2"
                  >
                    <div className="flex items-center gap-3">
                      <span className={`text-[10px] px-2 py-0.5 rounded-full font-medium ${statusColor(task.status)}`}>
                        {task.status}
                      </span>
                      <span className="text-xs text-[var(--text)] truncate flex-1">{task.url}</span>
                      <span className="text-[10px] font-mono text-[var(--text-muted)]">{task.task_id}</span>
                      {(task.status === "running" || task.status === "queued") && (
                        <button
                          onClick={() => refreshTask(task.task_id)}
                          className="text-[10px] text-[var(--accent)] hover:underline"
                        >
                          refresh
                        </button>
                      )}
                      {(task.status === "running" || task.status === "queued") && (
                        <button
                          onClick={() => handlePauseTask(task.task_id)}
                          disabled={taskAction[task.task_id] === "pause" || getTaskProgress(task).pauseRequested}
                          className="text-[10px] text-[var(--yellow)] hover:underline disabled:opacity-50"
                        >
                          {getTaskProgress(task).pauseRequested
                            ? "stop requested"
                            : taskAction[task.task_id] === "pause"
                              ? "stopping..."
                              : "stop"}
                        </button>
                      )}
                      {task.status === "paused" && (
                        <button
                          onClick={() => handleResumeTask(task.task_id)}
                          disabled={taskAction[task.task_id] === "resume"}
                          className="text-[10px] text-[var(--accent)] hover:underline disabled:opacity-50"
                        >
                          {taskAction[task.task_id] === "resume" ? "resuming..." : "resume"}
                        </button>
                      )}
                    </div>

                    {(() => {
                      const prog = getTaskProgress(task);
                      const showProgress = task.status === "running" || task.status === "queued" || task.status === "paused" || task.status === "completed";
                      if (!showProgress) return null;
                      return (
                        <div className="space-y-1.5">
                          <div className="flex items-center justify-between text-[11px] text-[var(--text-muted)]">
                            <span className="capitalize">{prog.phase}</span>
                            <span>{prog.completion.toFixed(0)}%</span>
                          </div>
                          <div className="h-1.5 rounded-full bg-[var(--bg-tertiary)] overflow-hidden">
                            <div
                              className="h-full bg-[var(--accent)] transition-all duration-300"
                              style={{ width: `${prog.completion}%` }}
                            />
                          </div>
                          {(prog.total > 0 || prog.currentTitle || prog.currentUrl) && (
                            <div className="text-[11px] text-[var(--text-muted)] space-y-0.5">
                              {prog.total > 0 && (
                                <p>
                                  crawled {prog.crawled}/{prog.total} · processed {prog.processed}/{prog.total}
                                  {prog.failed > 0 ? ` · failed ${prog.failed}` : ""}
                                </p>
                              )}
                              {prog.currentDoc > 0 && prog.total > 0 && (
                                <p>active doc: {Math.min(prog.currentDoc, prog.total)}/{prog.total}</p>
                              )}
                              {prog.currentTitle && <p className="truncate">current: {prog.currentTitle}</p>}
                              {!prog.currentTitle && prog.currentUrl && <p className="truncate">current: {prog.currentUrl}</p>}
                            </div>
                          )}
                          {prog.pauseRequested && task.status !== "paused" && (
                            <p className="text-[11px] text-[var(--yellow)]">
                              stop requested; waiting for safe checkpoint before showing resume.
                            </p>
                          )}
                          {prog.metadata && (
                            <div className="pt-1 text-[11px] text-[var(--text-muted)] space-y-0.5">
                              <p>
                                labels: type=<span className="text-[var(--text-secondary)]">{prog.metadata.doc_type || "-"}</span>
                                {" "}section=<span className="text-[var(--text-secondary)]">{prog.metadata.section || "-"}</span>
                                {" "}via {prog.metadata.llm_used ? "LLM" : "heuristics"}
                              </p>
                              {!!prog.metadata.tax_topics?.length && (
                                <p className="truncate">topics: {prog.metadata.tax_topics.join(", ")}</p>
                              )}
                              {!!prog.metadata.metadata_tags?.length && (
                                <p className="truncate">tags: {prog.metadata.metadata_tags.join(", ")}</p>
                              )}
                            </div>
                          )}
                          {!!prog.pipelineStage && (
                            <div className="pt-1 space-y-1.5">
                              <p className="text-[11px] text-[var(--text-muted)]">
                                pipeline stage: <span className="font-medium text-[var(--text-secondary)]">{prog.pipelineStage}</span>
                              </p>
                              <div className="flex flex-wrap gap-1.5">
                                {PIPELINE_STEPS.map(({ key, label }) => {
                                  const state = prog.pipelineSteps[key] || "pending";
                                  return (
                                    <span
                                      key={key}
                                      className={`text-[10px] px-2 py-0.5 rounded-full border font-medium ${stepClass(state)}`}
                                    >
                                      {label}
                                    </span>
                                  );
                                })}
                              </div>
                            </div>
                          )}
                        </div>
                      );
                    })()}

                    {/* Stats */}
                    {task.stats && (
                      <div className="flex gap-4 text-xs text-[var(--text-muted)]">
                        {task.stats.title && <span>{task.stats.title}</span>}
                        <span>{task.stats.parents} parents</span>
                        <span>{task.stats.children} children</span>
                        {task.stats.document_type && <span>{task.stats.document_type}</span>}
                      </div>
                    )}

                    {/* Error */}
                    {task.error && (
                      <p className="text-xs text-[var(--red)]">{task.error}</p>
                    )}

                    {/* Logs */}
                    {task.logs.length > 0 && (
                      <div
                        className="bg-[var(--bg-secondary)] rounded-lg p-2 max-h-28 overflow-y-auto"
                        ref={(el) => { if (el) el.scrollTop = el.scrollHeight; }}
                      >
                        {task.logs.map((log, i) => (
                          <p key={i} className="text-[11px] font-mono text-[var(--text-secondary)] leading-5">
                            <span className="text-[var(--text-muted)] mr-1.5">&gt;</span>{log}
                          </p>
                        ))}
                      </div>
                    )}
                  </div>
                ))}
              </div>

              {failedTasks.length > 0 && (
                <div className="pt-2 border-t border-[var(--border-light)]">
                  <div className="flex items-center justify-between">
                    <h4 className="text-xs font-medium text-[var(--text-secondary)]">
                      Failed Tasks ({failedTasks.length})
                    </h4>
                    <button
                      onClick={() => setShowFailed((v) => !v)}
                      className="text-xs text-[var(--accent)] hover:underline"
                    >
                      {showFailed ? "Hide" : "Show"}
                    </button>
                  </div>

                  {showFailed && (
                    <div className="mt-2 space-y-2">
                      {failedTasks.map((task) => (
                        <div
                          key={task.task_id}
                          className="border border-[var(--red-light)] rounded-lg p-3 space-y-2 bg-[var(--surface)]"
                        >
                          <div className="flex items-center gap-3">
                            <span className="text-[10px] px-2 py-0.5 rounded-full font-medium bg-[var(--red-light)] text-[var(--red)]">
                              failed
                            </span>
                            <span className="text-xs text-[var(--text)] truncate flex-1">{task.url}</span>
                            <span className="text-[10px] font-mono text-[var(--text-muted)]">{task.task_id}</span>
                          </div>
                          {task.error && (
                            <p className="text-xs text-[var(--red)]">{task.error}</p>
                          )}
                          {task.logs.length > 0 && (
                            <div className="bg-[var(--bg-secondary)] rounded-lg p-2 max-h-24 overflow-y-auto">
                              {task.logs.slice(-6).map((log, i) => (
                                <p key={i} className="text-[11px] font-mono text-[var(--text-secondary)] leading-5">
                                  <span className="text-[var(--text-muted)] mr-1.5">&gt;</span>{log}
                                </p>
                              ))}
                            </div>
                          )}
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
