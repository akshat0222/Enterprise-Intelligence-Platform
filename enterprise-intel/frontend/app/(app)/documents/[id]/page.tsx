"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { api, Chunk, Doc } from "@/lib/api";

const PAGE = 20;

export default function DocumentDetail() {
  const { id } = useParams<{ id: string }>();
  const [doc, setDoc] = useState<Doc | null>(null);
  const [chunks, setChunks] = useState<Chunk[]>([]);
  const [more, setMore] = useState(true);
  const [error, setError] = useState("");

  const loadChunks = useCallback(async (after: number) => {
    const page = await api<Chunk[]>(`/documents/${id}/chunks?after=${after}&limit=${PAGE}`);
    setChunks((prev) => (after < 0 ? page : [...prev, ...page]));
    setMore(page.length === PAGE);
  }, [id]);

  useEffect(() => {
    api<Doc>(`/documents/${id}`)
      .then((d) => { setDoc(d); return d.status === "ready" ? loadChunks(-1) : undefined; })
      .catch((e) => setError(e instanceof Error ? e.message : "Failed to load document"));
  }, [id, loadChunks]);

  if (error) return <main><p className="error">{error}</p><Link href="/documents">← Documents</Link></main>;
  if (!doc) return <main><p className="muted">Loading…</p></main>;
  const s = doc.stats;

  return (
    <main>
      <p><Link href="/documents">← Documents</Link></p>
      <div className="row">
        <h1>{doc.filename}</h1>
        <span className={`badge ${doc.status}`}>{doc.status}</span>
      </div>
      {doc.error_message && <p className="error">{doc.error_message}</p>}

      {s && (
        <>
          <div className="kpis">
            {([["Words", s.words], ["Tokens", s.tokens], ["Chunks", s.chunks], ["Pages", s.pages], ["Sections", s.sections], ["Tables", s.tables], ["Images removed", s.images_removed], ["Flagged chunks", s.flagged_chunks]] as [string, number | undefined][])
              .map(([k, v]) => <div className="card kpi" key={k}><div className="muted">{k}</div><div className="kpi-value">{v?.toLocaleString() ?? "–"}</div></div>)}
          </div>
          <div className="two-col">
            <div className="card">
              <h3>Detected headings</h3>
              {s.headings?.length ? (
                <ul className="outline">{s.headings.map((h, i) => <li key={i} style={{ paddingLeft: (h.level - 1) * 16 }}>{h.text}{h.page ? <span className="muted"> · p.{h.page}</span> : null}</li>)}</ul>
              ) : <p className="muted">No headings detected.</p>}
            </div>
            <div className="card">
              <h3>Top keywords</h3>
              <div>{s.keywords?.map((k) => <span key={k.word} className="badge" style={{ margin: 3 }}>{k.word} · {k.count}</span>)}</div>
            </div>
          </div>
        </>
      )}

      {doc.status === "ready" && (
        <div className="card" style={{ marginTop: 16 }}>
          <h3>Chunks</h3>
          {chunks.map((c) => (
            <details key={c.id} className="chunk">
              <summary>
                <strong>#{c.chunk_index}</strong> <span className="badge">{c.content_type}</span>{" "}
                {c.heading_path?.join(" › ")} {c.page_start ? <span className="muted">· p.{c.page_start}</span> : null}
                <span className="muted"> · {c.token_count} tokens</span>
                {c.flagged_injection && <span className="badge failed" style={{ marginLeft: 6 }}>possible prompt injection</span>}
              </summary>
              <pre>{c.content}</pre>
            </details>
          ))}
          {more && <button className="secondary" onClick={() => loadChunks(chunks[chunks.length - 1]?.chunk_index ?? -1)}>Load more</button>}
        </div>
      )}
    </main>
  );
}
