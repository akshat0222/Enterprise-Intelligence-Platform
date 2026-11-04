"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  api, ApiError, Citation, Conflict, ConversationDetail, Conversation, Details, Doc, jsonBody, Message, Step, streamChat,
} from "@/lib/api";

type Tab = "sources" | "trace" | "details";

type Live = {
  route?: string;
  steps: Step[];
  text: string;
  citations: Citation[];
  conflicts: Conflict[];
  verification?: { groundedness: number | null; issues: string[] };
};

const EMPTY_LIVE: Live = { steps: [], text: "", citations: [], conflicts: [] };

export default function ChatPage() {
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [docs, setDocs] = useState<Doc[]>([]);
  const [models, setModels] = useState<{ model: string; provider: string }[]>([]);
  const [scope, setScope] = useState<string[]>([]);
  const [model, setModel] = useState("");
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [live, setLive] = useState<Live | null>(null);
  const [pendingQuestion, setPendingQuestion] = useState("");
  const [error, setError] = useState("");
  const [tab, setTab] = useState<Tab>("sources");
  const [selected, setSelected] = useState<string | null>(null);
  const [highlight, setHighlight] = useState<string | null>(null);
  const bottom = useRef<HTMLDivElement>(null);

  const loadConversations = useCallback(async () => {
    setConversations(await api<Conversation[]>("/conversations"));
  }, []);

  const openConversation = useCallback(async (id: string) => {
    const detail = await api<ConversationDetail>(`/conversations/${id}`);
    setActiveId(id);
    setMessages(detail.messages);
    setSelected(null);
  }, []);

  useEffect(() => {
    (async () => {
      try {
        const [convs, allDocs, modelList] = await Promise.all([
          api<Conversation[]>("/conversations"),
          api<Doc[]>("/documents"),
          api<{ models: { model: string; provider: string }[] }>("/models"),
        ]);
        setConversations(convs);
        setDocs(allDocs.filter((d) => d.status === "ready"));
        setModels(modelList.models);
        if (convs.length) await openConversation(convs[0].id);
      } catch (e) {
        setError(e instanceof Error ? e.message : "Failed to load chat");
      }
    })();
  }, [openConversation]);

  useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, live?.text, live?.steps.length]);

  const lastAssistant = useMemo(() => [...messages].reverse().find((m) => m.role === "assistant"), [messages]);
  const shown = messages.find((m) => m.id === selected) ?? lastAssistant;

  async function newConversation() {
    const conv = await api<Conversation>("/conversations", jsonBody({}));
    await loadConversations();
    setActiveId(conv.id);
    setMessages([]);
    setSelected(null);
    return conv.id;
  }

  async function rename(c: Conversation) {
    const title = prompt("Rename conversation", c.title ?? "");
    if (title?.trim()) {
      await api(`/conversations/${c.id}`, { ...jsonBody({ title }), method: "PATCH" });
      loadConversations();
    }
  }

  async function remove(c: Conversation) {
    if (!confirm("Delete this conversation?")) return;
    await api(`/conversations/${c.id}`, { method: "DELETE" });
    const rest = await api<Conversation[]>("/conversations");
    setConversations(rest);
    if (c.id === activeId) {
      if (rest.length) await openConversation(rest[0].id);
      else { setActiveId(null); setMessages([]); }
    }
  }

  async function send() {
    const content = draft.trim();
    if (!content || busy) return;
    setError("");
    setBusy(true);
    setDraft("");
    setPendingQuestion(content);
    setLive({ ...EMPTY_LIVE });
    try {
      const id = activeId ?? (await newConversation());
      await streamChat(id, { content, document_ids: scope, model: model || undefined }, (event, data) => {
        setLive((prev) => {
          const cur = prev ?? { ...EMPTY_LIVE };
          switch (event) {
            case "route": return { ...cur, route: data.route };
            case "tool_start": return { ...cur, steps: [...cur.steps, { step_no: data.step, tool_name: data.tool, args: data.args, output_summary: null, status: "running", error: null, latency_ms: null }] };
            case "tool_end": return { ...cur, steps: cur.steps.map((s) => s.step_no === data.step ? { ...s, status: data.status, output_summary: data.summary, error: data.error ?? null, latency_ms: data.latency_ms } : s) };
            case "verification": return { ...cur, verification: data };
            case "citations": return { ...cur, citations: data.citations, conflicts: data.conflicts };
            case "token": return { ...cur, text: cur.text + data.text };
            case "error": setError(data.message); return cur;
            default: return cur;
          }
        });
      });
      await openConversation(id);
      loadConversations();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Message failed");
    } finally {
      setBusy(false);
      setLive(null);
      setPendingQuestion("");
    }
  }

  function toggleScope(id: string) {
    setScope((s) => (s.includes(id) ? s.filter((x) => x !== id) : [...s, id]));
  }

  function jumpToSource(messageId: string | null, sourceId: string) {
    if (messageId) setSelected(messageId);
    setTab("sources");
    setHighlight(sourceId);
  }

  return (
    <div className="chat">
      <aside className="chat-list">
        <button onClick={newConversation} style={{ width: "100%" }}>+ New chat</button>
        {conversations.length === 0 && <p className="muted">No conversations yet.</p>}
        {conversations.map((c) => (
          <div key={c.id} className={`conv ${c.id === activeId ? "active" : ""}`} onClick={() => openConversation(c.id)}>
            <span className="conv-title">{c.title ?? "New chat"}</span>
            <span className="conv-actions">
              <button className="icon" title="Rename" onClick={(e) => { e.stopPropagation(); rename(c); }}>✎</button>
              <button className="icon" title="Delete" onClick={(e) => { e.stopPropagation(); remove(c); }}>✕</button>
            </span>
          </div>
        ))}
      </aside>

      <section className="chat-main">
        <div className="messages">
          {messages.length === 0 && !live && (
            <div className="empty">
              <h2>Ask about your documents</h2>
              <p className="muted">Answers are grounded in your uploads and cite their sources. Try a question, or ask for an analysis report.</p>
            </div>
          )}
          {messages.map((m) => (
            <MessageView key={m.id} m={m} active={m.id === shown?.id} onSelect={() => setSelected(m.id)}
              onCite={(sid) => jumpToSource(m.id, sid)} />
          ))}
          {live && (
            <>
              <div className="msg user"><div className="bubble">{pendingQuestion}</div></div>
              <div className="msg assistant">
                <div className="bubble">
                  {live.steps.length > 0 && <LiveSteps steps={live.steps} />}
                  {live.text ? <Rich text={live.text} onCite={() => {}} /> : <span className="muted">{live.route ? (live.route === "agent" ? "Working through the steps…" : "Searching your documents…") : "Thinking…"}</span>}
                </div>
              </div>
            </>
          )}
          <div ref={bottom} />
        </div>

        {error && <p className="error" style={{ margin: "0 16px" }}>{error}</p>}
        <div className="composer">
          <div className="pickers">
            <details className="scope">
              <summary>{scope.length ? `${scope.length} document${scope.length > 1 ? "s" : ""} selected` : "All documents"}</summary>
              <div className="scope-list">
                {docs.length === 0 && <span className="muted">No ready documents</span>}
                {docs.map((d) => (
                  <label key={d.id}><input type="checkbox" checked={scope.includes(d.id)} onChange={() => toggleScope(d.id)} /> {d.filename}</label>
                ))}
                {scope.length > 0 && <button className="secondary" onClick={() => setScope([])}>Clear</button>}
              </div>
            </details>
            <select value={model} onChange={(e) => setModel(e.target.value)} aria-label="Model">
              <option value="">Model: auto</option>
              {models.map((m) => <option key={m.model} value={m.model}>{m.model}</option>)}
            </select>
          </div>
          <div className="input-row">
            <textarea value={draft} rows={2} placeholder="Ask a question…" disabled={busy}
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); } }} />
            <button onClick={send} disabled={busy || !draft.trim()}>{busy ? "…" : "Send"}</button>
          </div>
        </div>
      </section>

      <aside className="chat-side">
        <div className="tabs">
          {(["sources", "trace", "details"] as Tab[]).map((t) => (
            <button key={t} className={tab === t ? "on" : ""} onClick={() => setTab(t)}>
              {t === "sources" ? "Sources" : t === "trace" ? "Agent trace" : "Details"}
            </button>
          ))}
        </div>
        <div className="side-body">
          {live && tab === "trace" ? <LiveSteps steps={live.steps} detailed />
            : !shown ? <p className="muted">Nothing to show yet.</p>
            : tab === "sources" ? <SourcesPanel m={shown} highlight={highlight} />
            : tab === "trace" ? <TracePanel steps={shown.steps} route={shown.route} />
            : <DetailsPanel m={shown} />}
        </div>
      </aside>
    </div>
  );
}

function Rich({ text, onCite }: { text: string; onCite: (sourceId: string) => void }) {
  const linked = text.replace(/\[(S\d+)\]/g, "[$1](#cite-$1)");
  return (
    <div className="md">
      <Markdown remarkPlugins={[remarkGfm]} components={{
        a: ({ href, children }) => {
          if (href?.startsWith("#cite-")) {
            const id = href.slice(6);
            return <button className="chip" onClick={() => onCite(id)}>{id}</button>;
          }
          return <a href={href} target="_blank" rel="noreferrer noopener">{children}</a>;
        },
      }}>{linked}</Markdown>
    </div>
  );
}

function MessageView({ m, active, onSelect, onCite }: { m: Message; active: boolean; onSelect: () => void; onCite: (id: string) => void }) {
  if (m.role === "user") return <div className="msg user"><div className="bubble">{m.content}</div></div>;
  const conflicts = m.details?.conflicts ?? [];
  return (
    <div className={`msg assistant ${active ? "selected" : ""}`} onClick={onSelect}>
      <div className="bubble">
        {m.status === "insufficient_evidence" && <div className="card warn">Not found in your documents — nothing relevant was retrieved, so no answer was generated.</div>}
        {m.status === "conflicting_sources" && conflicts.length > 0 && (
          <div className="card conflict">
            <strong>⚠ Sources disagree</strong>
            {conflicts.map((c, i) => (
              <div key={i}>
                <div>{c.topic}</div>
                <div className="muted">{c.description}</div>
                <div>{c.sources.map((s) => <button key={s} className="chip" onClick={() => onCite(s)}>{s}</button>)}</div>
              </div>
            ))}
          </div>
        )}
        {m.status === "error" && <div className="card bad">Something went wrong.</div>}
        <Rich text={m.content} onCite={onCite} />
        <div className="meta">
          {m.route && <span className="badge">{m.route}</span>}
          {m.groundedness_score != null && <span className={`badge ${m.groundedness_score >= 0.6 ? "ready" : "failed"}`}>grounded {Math.round(m.groundedness_score * 100)}%</span>}
          {m.details?.fallback_used && <span className="badge processing">fallback model</span>}
          {m.details?.issues?.map((i, k) => <span key={k} className="badge failed" title={i}>⚠ {i.slice(0, 40)}</span>)}
        </div>
      </div>
    </div>
  );
}

function StepRow({ s, detailed }: { s: Step; detailed?: boolean }) {
  return (
    <div className="step">
      <div className="step-head">
        <strong>{s.step_no}. {s.tool_name}</strong>
        <span className={`badge ${s.status === "ok" ? "ready" : s.status === "running" ? "processing" : "failed"}`}>{s.status}</span>
        {s.latency_ms != null && <span className="muted">{s.latency_ms} ms</span>}
      </div>
      {s.error && <div className="error">{s.error}</div>}
      {detailed && (
        <>
          {s.args && <pre>{JSON.stringify(s.args, null, 1)}</pre>}
          {s.output_summary && <pre>{JSON.stringify(s.output_summary, null, 1).slice(0, 1200)}</pre>}
        </>
      )}
    </div>
  );
}

function LiveSteps({ steps, detailed }: { steps: Step[]; detailed?: boolean }) {
  if (!steps.length) return <p className="muted">No steps yet.</p>;
  return <div className="steps">{steps.map((s, i) => <StepRow key={i} s={s} detailed={detailed} />)}</div>;
}

function SourcesPanel({ m, highlight }: { m: Message; highlight: string | null }) {
  const cites = m.citations ?? [];
  if (!cites.length) return <p className="muted">This answer has no sources.</p>;
  return (
    <div>
      {cites.map((c) => (
        <div key={c.source_id} className={`source ${highlight === c.source_id ? "hl" : ""}`}>
          <div className="step-head"><span className="chip">{c.source_id}</span><strong>{c.filename}</strong></div>
          <div className="muted">{[c.page ? `page ${c.page}` : null, c.section].filter(Boolean).join(" · ")}</div>
          {c.quote && <blockquote>“{c.quote}”</blockquote>}
          {m.details?.flagged_sources?.includes(c.source_id) && <div className="card warn">Contains instruction-like text (flagged as a possible prompt injection).</div>}
        </div>
      ))}
    </div>
  );
}

function TracePanel({ steps, route }: { steps: Step[]; route: string | null }) {
  if (!steps.length) return <p className="muted">{route === "agent" ? "No steps recorded." : `Route: ${route ?? "–"}. Only agent runs have tool steps.`}</p>;
  return <LiveSteps steps={steps} detailed />;
}

function DetailsPanel({ m }: { m: Message }) {
  const d: Details = m.details ?? {};
  const rows: [string, string][] = [
    ["Route", m.route ?? "–"],
    ["Status", m.status ?? "–"],
    ["Models", d.models?.join(", ") || "–"],
    ["Tokens in / out", `${d.input_tokens ?? 0} / ${d.output_tokens ?? 0}`],
    ["LLM calls", String(d.llm_calls ?? 0)],
    ["Cost (est.)", `$${(d.cost_usd ?? 0).toFixed(4)}`],
    ["Latency", d.latency_ms != null ? `${(d.latency_ms / 1000).toFixed(1)} s` : "–"],
    ["Groundedness", m.groundedness_score != null ? m.groundedness_score.toFixed(2) : "–"],
    ["Confidence", d.confidence ?? "–"],
    ["Fallback used", d.fallback_used ? "yes" : "no"],
  ];
  return (
    <div>
      <table><tbody>{rows.map(([k, v]) => <tr key={k}><td className="muted">{k}</td><td>{v}</td></tr>)}</tbody></table>
      {!!d.issues?.length && <><h4>Verification issues</h4><ul>{d.issues.map((i, k) => <li key={k}>{i}</li>)}</ul></>}
    </div>
  );
}
