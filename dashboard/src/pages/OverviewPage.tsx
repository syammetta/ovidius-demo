const STACK = [
  "FastAPI",
  "Anthropic Claude",
  "Supabase Postgres",
  "pgvector",
  "Voyage Embeddings",
  "FlashRank Reranker",
  "OpenTelemetry",
  "React + Vite",
];

const PIPELINE_STEPS = [
  {
    title: "Ingest and Normalize",
    body: "Crawl docs, parse pages, and produce clean parent-child chunks tuned for retrieval precision and generation context.",
  },
  {
    title: "Contextual Embedding",
    body: "Generate contextualized child chunks and embed with Voyage before storing vectors + metadata in Postgres/pgvector.",
  },
  {
    title: "Hybrid Retrieval",
    body: "Run dense vector search + BM25, fuse with reciprocal rank fusion, then rerank with cross-encoder scoring.",
  },
  {
    title: "Corrective RAG Gate",
    body: "Assess retrieval confidence, filter weak passages, and retry transformed queries when quality is below threshold.",
  },
  {
    title: "Citation-Grounded Answering",
    body: "Generate answers with explicit numbered citations mapped to source URLs and titles for traceability.",
  },
  {
    title: "Continuous Evaluation",
    body: "Track recall and faithfulness across curated QA pairs and live traffic to ensure quality stays measurable.",
  },
];

const BUILD_NOTES = [
  "Single retrieval core is shared across API, agent, dashboard, and tool surfaces.",
  "Parent-child chunking keeps retrieval precise while preserving context for generation.",
  "Confidence-aware generation is used to reduce confident hallucinations.",
  "Every query emits telemetry and pipeline timing for observability-first debugging.",
];

const REVIEWER_PATH = [
  { label: "1. Read pipeline strategy", page: "overview", hint: "Start here" },
  { label: "2. Run live Q&A", page: "ask", hint: "Check citations + confidence" },
  { label: "3. Inspect evaluation", page: "eval", hint: "Review quality metrics" },
  { label: "4. Open traces", page: "traces", hint: "Validate stage-by-stage latency" },
];

interface Props {
  onNavigate: (page: "ask" | "documents" | "ingest" | "traces" | "eval") => void;
}

export default function OverviewPage({ onNavigate }: Props) {
  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto max-w-6xl px-8 py-10 space-y-8">
        <section
          className="relative overflow-hidden rounded-3xl border border-[var(--border-light)] bg-[var(--surface)] p-8 md:p-10"
          style={{ boxShadow: "var(--shadow-md)" }}
        >
          <div className="absolute -top-20 -right-20 h-64 w-64 rounded-full bg-[var(--accent-light)] opacity-70" />
          <div className="absolute -bottom-24 -left-24 h-72 w-72 rounded-full bg-[var(--purple-light)] opacity-80" />

          <div className="relative z-10 space-y-7">
            <span className="inline-flex items-center rounded-full border border-[var(--accent)]/30 bg-[var(--accent-light)] px-3 py-1 text-xs font-medium text-[var(--accent)]">
              Senior AI Builder Qualification Project
            </span>

            <div className="grid grid-cols-1 lg:grid-cols-[minmax(0,1fr)_280px] gap-6">
              <div className="max-w-3xl space-y-5">
                <h1 className="text-3xl md:text-4xl font-semibold tracking-tight text-[var(--text)]">
                  Production-style RAG agent for public documentation QA
                </h1>

                <p className="text-base md:text-lg text-[var(--text-secondary)] leading-relaxed">
                  This demo focuses on what matters in production AI systems:
                  retrieval quality, citation fidelity, measurable evaluation, and
                  transparent observability from request to response.
                </p>

                <div className="flex flex-wrap gap-3 pt-1">
                  <button
                    onClick={() => onNavigate("ask")}
                    className="px-4 py-2 rounded-xl bg-[var(--accent)] text-white text-sm font-medium hover:opacity-90 transition-opacity"
                  >
                    Open Live Q&A
                  </button>
                  <button
                    onClick={() => onNavigate("eval")}
                    className="px-4 py-2 rounded-xl border border-[var(--border)] text-[var(--text)] text-sm font-medium hover:bg-[var(--bg-secondary)] transition-colors"
                  >
                    View Evaluation
                  </button>
                  <button
                    onClick={() => onNavigate("traces")}
                    className="px-4 py-2 rounded-xl border border-[var(--border)] text-[var(--text)] text-sm font-medium hover:bg-[var(--bg-secondary)] transition-colors"
                  >
                    Inspect Traces
                  </button>
                </div>
              </div>

              <aside className="rounded-2xl border border-[var(--border-light)] bg-white/70 p-4 backdrop-blur-sm">
                <h2 className="text-sm font-semibold text-[var(--text)]">Reviewer quick path</h2>
                <p className="mt-1 text-xs text-[var(--text-muted)]">
                  Fast way to evaluate build quality in under 5 minutes.
                </p>
                <div className="mt-3 space-y-2">
                  {REVIEWER_PATH.filter((item) => item.page !== "overview").map((item) => (
                    <button
                      key={item.label}
                      onClick={() => onNavigate(item.page as "ask" | "documents" | "ingest" | "traces" | "eval")}
                      className="w-full rounded-lg border border-[var(--border)] bg-[var(--surface)] px-3 py-2 text-left hover:bg-[var(--bg-secondary)] transition-colors"
                    >
                      <p className="text-xs font-medium text-[var(--text)]">{item.label}</p>
                      <p className="text-[11px] text-[var(--text-muted)]">{item.hint}</p>
                    </button>
                  ))}
                </div>
              </aside>
            </div>
          </div>
        </section>

        <section
          className="rounded-2xl border border-[var(--border-light)] bg-[var(--surface)] p-6"
          style={{ boxShadow: "var(--shadow-sm)" }}
        >
          <h2 className="text-lg font-semibold text-[var(--text)]">Tech Stack</h2>
          <p className="mt-1 text-sm text-[var(--text-muted)]">
            Components used to ship quickly while keeping the architecture production-aligned.
          </p>
          <div className="mt-4 flex flex-wrap gap-2.5">
            {STACK.map((item) => (
              <span
                key={item}
                className="rounded-full border border-[var(--border)] bg-[var(--bg-secondary)] px-3 py-1 text-xs font-medium text-[var(--text-secondary)]"
              >
                {item}
              </span>
            ))}
          </div>
        </section>

        <section
          className="rounded-2xl border border-[var(--border-light)] bg-[var(--surface)] p-6"
          style={{ boxShadow: "var(--shadow-sm)" }}
        >
          <h2 className="text-lg font-semibold text-[var(--text)]">RAG Pipeline Design</h2>
          <p className="mt-1 text-sm text-[var(--text-muted)]">
            Retrieval-first architecture that emphasizes precision, confidence, and citation integrity.
          </p>

          <div className="mt-5 grid grid-cols-1 md:grid-cols-2 gap-4">
            {PIPELINE_STEPS.map((step, idx) => (
              <div
                key={step.title}
                className="rounded-xl border border-[var(--border-light)] bg-[var(--bg-secondary)] p-4"
              >
                <div className="flex items-center gap-2">
                  <span className="inline-flex h-6 w-6 items-center justify-center rounded-full bg-[var(--accent-light)] text-xs font-semibold text-[var(--accent)]">
                    {idx + 1}
                  </span>
                  <h3 className="text-sm font-semibold text-[var(--text)]">{step.title}</h3>
                </div>
                <p className="mt-2 text-sm leading-relaxed text-[var(--text-secondary)]">{step.body}</p>
              </div>
            ))}
          </div>
        </section>

        <section className="grid grid-cols-1 md:grid-cols-3 gap-4">
          <article
            className="rounded-2xl border border-[var(--border-light)] bg-[var(--surface)] p-5"
            style={{ boxShadow: "var(--shadow-sm)" }}
          >
            <h3 className="text-sm font-semibold text-[var(--text)]">Evaluation-first delivery</h3>
            <p className="mt-2 text-sm leading-relaxed text-[var(--text-secondary)]">
              The system is judged on retrieval and answer quality using a curated QA set,
              then continuously tracked via live dashboard usage.
            </p>
          </article>
          <article
            className="rounded-2xl border border-[var(--border-light)] bg-[var(--surface)] p-5"
            style={{ boxShadow: "var(--shadow-sm)" }}
          >
            <h3 className="text-sm font-semibold text-[var(--text)]">Citations as contract</h3>
            <p className="mt-2 text-sm leading-relaxed text-[var(--text-secondary)]">
              Answers are generated with explicit source references so every core claim can
              be traced back to a document URL and title.
            </p>
          </article>
          <article
            className="rounded-2xl border border-[var(--border-light)] bg-[var(--surface)] p-5"
            style={{ boxShadow: "var(--shadow-sm)" }}
          >
            <h3 className="text-sm font-semibold text-[var(--text)]">Observability by default</h3>
            <p className="mt-2 text-sm leading-relaxed text-[var(--text-secondary)]">
              Pipeline stages, confidence signals, and latency are captured to make failures
              diagnosable instead of hidden.
            </p>
          </article>
        </section>

        <section className="grid grid-cols-1 lg:grid-cols-3 gap-4">
          <article
            className="lg:col-span-2 rounded-2xl border border-[var(--border-light)] bg-[var(--surface)] p-6"
            style={{ boxShadow: "var(--shadow-sm)" }}
          >
            <h2 className="text-lg font-semibold text-[var(--text)]">What this demonstrates</h2>
            <div className="mt-4 space-y-3">
              {BUILD_NOTES.map((note) => (
                <div key={note} className="flex items-start gap-2.5">
                  <span className="mt-1 inline-block h-2 w-2 rounded-full bg-[var(--accent)]" />
                  <p className="text-sm text-[var(--text-secondary)] leading-relaxed">{note}</p>
                </div>
              ))}
            </div>
          </article>

          <article
            className="rounded-2xl border border-[var(--border-light)] bg-[var(--surface)] p-6"
            style={{ boxShadow: "var(--shadow-sm)" }}
          >
            <h2 className="text-lg font-semibold text-[var(--text)]">Explore the demo</h2>
            <div className="mt-4 space-y-2">
              <button
                onClick={() => onNavigate("ingest")}
                className="w-full text-left rounded-lg border border-[var(--border)] px-3 py-2.5 text-sm text-[var(--text)] hover:bg-[var(--bg-secondary)] transition-colors"
              >
                Ingestion Workflow
              </button>
              <button
                onClick={() => onNavigate("documents")}
                className="w-full text-left rounded-lg border border-[var(--border)] px-3 py-2.5 text-sm text-[var(--text)] hover:bg-[var(--bg-secondary)] transition-colors"
              >
                Indexed Documents
              </button>
              <button
                onClick={() => onNavigate("ask")}
                className="w-full text-left rounded-lg border border-[var(--border)] px-3 py-2.5 text-sm text-[var(--text)] hover:bg-[var(--bg-secondary)] transition-colors"
              >
                Ask Questions
              </button>
              <button
                onClick={() => onNavigate("traces")}
                className="w-full text-left rounded-lg border border-[var(--border)] px-3 py-2.5 text-sm text-[var(--text)] hover:bg-[var(--bg-secondary)] transition-colors"
              >
                Trace Timeline
              </button>
            </div>
          </article>
        </section>
      </div>
    </div>
  );
}
