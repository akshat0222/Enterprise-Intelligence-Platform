"use client";

import { useRouter } from "next/navigation";
import { DragEvent, useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError, Doc, logout, refreshSession } from "@/lib/api";

const POLL_MS = 2000;

export default function DocumentsPage() {
  const router = useRouter();
  const [docs, setDocs] = useState<Doc[] | null>(null);
  const [toasts, setToasts] = useState<string[]>([]);
  const [error, setError] = useState("");
  const [over, setOver] = useState(false);
  const input = useRef<HTMLInputElement>(null);

  const load = useCallback(async () => {
    try {
      setDocs(await api<Doc[]>("/documents"));
      setError("");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load documents");
    }
  }, []);

  useEffect(() => {
    refreshSession().then((ok) => (ok ? load() : router.replace("/login")));
  }, [load, router]);

  const active = docs?.some((d) => d.status === "uploaded" || d.status === "processing");
  useEffect(() => {
    if (!active) return;
    const t = setInterval(load, POLL_MS);
    return () => clearInterval(t);
  }, [active, load]);

  async function upload(files: FileList | File[]) {
    for (const file of Array.from(files)) {
      const form = new FormData();
      form.append("file", file);
      try {
        const doc = await api<Doc>("/documents", { method: "POST", body: form });
        if (doc.duplicate) setToasts((t) => [...t, `${file.name} was already uploaded as ${doc.filename}`]);
      } catch (e) {
        setToasts((t) => [...t, `${file.name}: ${e instanceof ApiError ? e.message : "upload failed"}`]);
      }
    }
    load();
  }

  async function act(path: string, method: string) {
    try {
      await api(path, { method });
    } catch (e) {
      setToasts((t) => [...t, e instanceof Error ? e.message : "Action failed"]);
    }
    load();
  }

  function onDrop(e: DragEvent) {
    e.preventDefault();
    setOver(false);
    upload(e.dataTransfer.files);
  }

  return (
    <main>
      <div className="row">
        <h1>Documents</h1>
        <button className="secondary" onClick={async () => { await logout(); router.push("/login"); }}>
          Sign out
        </button>
      </div>

      <div
        className={`drop ${over ? "over" : ""}`}
        onDragOver={(e) => { e.preventDefault(); setOver(true); }}
        onDragLeave={() => setOver(false)}
        onDrop={onDrop}
      >
        <p style={{ margin: "0 0 10px" }}>Drag PDF, TXT, MD or CSV files here</p>
        <button onClick={() => input.current?.click()}>Choose files</button>
        <input ref={input} type="file" multiple hidden accept=".pdf,.txt,.md,.csv" onChange={(e) => e.target.files && upload(e.target.files)} />
      </div>

      {toasts.map((t, i) => (
        <div className="toast" key={i}>
          {t} <button className="danger" onClick={() => setToasts((all) => all.filter((_, j) => j !== i))}>×</button>
        </div>
      ))}
      {error && <p className="error">{error}</p>}

      <div className="card">
        {docs === null ? (
          <p className="muted">Loading…</p>
        ) : docs.length === 0 ? (
          <p className="muted">No documents yet. Upload one to get started.</p>
        ) : (
          <table>
            <thead>
              <tr><th>Name</th><th>Type</th><th>Size</th><th>Status</th><th>Words</th><th>Chunks</th><th>Tables</th><th /></tr>
            </thead>
            <tbody>
              {docs.map((d) => (
                <tr key={d.id}>
                  <td>{d.filename}</td>
                  <td>{d.file_type}</td>
                  <td>{d.size_bytes ? `${(d.size_bytes / 1024).toFixed(1)} KB` : "–"}</td>
                  <td>
                    <span className={`badge ${d.status}`}>{d.status}</span>
                    {d.error_message && <div className="muted">{d.error_message}</div>}
                  </td>
                  <td>{d.stats?.words ?? "–"}</td>
                  <td>{d.stats?.chunks ?? "–"}</td>
                  <td>{d.stats?.tables ?? "–"}</td>
                  <td style={{ whiteSpace: "nowrap" }}>
                    {(d.status === "failed" || d.status === "ready") && (
                      <button className="danger" onClick={() => act(`/documents/${d.id}/reprocess`, "POST")}>Reprocess</button>
                    )}{" "}
                    <button className="danger" onClick={() => confirm(`Delete ${d.filename}?`) && act(`/documents/${d.id}`, "DELETE")}>Delete</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </main>
  );
}
