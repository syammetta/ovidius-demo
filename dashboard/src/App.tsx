import { useState } from "react";
import Sidebar from "./components/Sidebar";
import type { Page } from "./components/Sidebar";
import AskPage from "./pages/AskPage";
import DocumentsPage from "./pages/DocumentsPage";
import IngestPage from "./pages/IngestPage";
import TracesPage from "./pages/TracesPage";
import EvalPage from "./pages/EvalPage";

function App() {
  const [page, setPage] = useState<Page>("ask");

  return (
    <div className="h-screen flex bg-[var(--bg-secondary)]">
      <Sidebar current={page} onNavigate={setPage} />
      <main className="flex-1 min-w-0 overflow-hidden">
        {page === "ask" && <AskPage />}
        {page === "documents" && <DocumentsPage />}
        {page === "ingest" && <IngestPage />}
        {page === "traces" && <TracesPage />}
        {page === "eval" && <EvalPage />}
      </main>
    </div>
  );
}

export default App;
