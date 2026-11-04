"use client";

import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";

type Activity = { id: number; action: string | null; entity_type: string | null; details: Record<string, unknown> | null; level: string; created_at: string };
type Call = { id: string; task: string | null; provider: string | null; model: string | null; input_tokens: number | null; output_tokens: number | null; cost_usd: number | null; latency_ms: number | null; success: boolean | null; error: string | null; fallback_used: boolean; created_at: string };

export default function LogsPage() {
  const [tab, setTab] = useState<"activity" | "llm">("activity");
  const [level, setLevel] = useState("");
  const [errorsOnly, setErrorsOnly] = useState(false);
  const [model, setModel] = useState("");
  const [activity, setActivity] = useState<Activity[] | null>(null);
  const [calls, setCalls] = useState<Call[] | null>(null);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    setError("");
    try {
      if (tab === "activity") setActivity(await api<Activity[]>(`/logs/activity?limit=100${level ? `&level=${level}` : ""}`));
      else setCalls(await api<Call[]>(`/logs/llm-calls?limit=100${errorsOnly ? "&errors_only=true" : ""}${model ? `&model=${encodeURIComponent(model)}` : ""}`));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load logs");
    }
  }, [tab, level, errorsOnly, model]);

  useEffect(() => { load(); }, [load]);

  return (
    <main>
      <div className="row">
        <h1>Logs</h1>
        <div className="tabs inline">
          <button className={tab === "activity" ? "on" : ""} onClick={() => setTab("activity")}>Activity</button>
          <button className={tab === "llm" ? "on" : ""} onClick={() => setTab("llm")}>LLM calls</button>
        </div>
      </div>
      <div className="filters">
        {tab === "activity" ? (
          <select value={level} onChange={(e) => setLevel(e.target.value)} aria-label="Level">
            <option value="">All levels</option><option value="info">info</option><option value="warning">warning</option><option value="error">error</option>
          </select>
        ) : (
          <>
            <label><input type="checkbox" checked={errorsOnly} onChange={(e) => setErrorsOnly(e.target.checked)} /> Errors only</label>
            <input placeholder="Filter by model, e.g. claude-sonnet-5-5" value={model} onChange={(e) => setModel(e.target.value)} style={{ maxWidth: 320, marginBottom: 0 }} />
          </>
        )}
        <button className="secondary" onClick={load}>Refresh</button>
      </div>
      {error && <p className="error">{error}</p>}

      <div className="card" style={{ overflowX: "auto" }}>
        {tab === "activity" ? (
          activity === null ? <p className="muted">Loading…</p> : activity.length === 0 ? <p className="muted">No activity matches.</p> : (
            <table>
              <thead><tr><th>Time</th><th>Action</th><th>Entity</th><th>Level</th><th>Details</th></tr></thead>
              <tbody>{activity.map((a) => (
                <tr key={a.id}>
                  <td className="muted" style={{ whiteSpace: "nowrap" }}>{new Date(a.created_at).toLocaleString()}</td>
                  <td>{a.action}</td><td className="muted">{a.entity_type}</td>
                  <td><span className={`badge ${a.level === "info" ? "ready" : a.level === "warning" ? "processing" : "failed"}`}>{a.level}</span></td>
                  <td className="muted">{a.details ? JSON.stringify(a.details).slice(0, 120) : ""}</td>
                </tr>
              ))}</tbody>
            </table>
          )
        ) : calls === null ? <p className="muted">Loading…</p> : calls.length === 0 ? <p className="muted">No LLM calls match.</p> : (
          <table>
            <thead><tr><th>Time</th><th>Task</th><th>Model</th><th>Tokens in/out</th><th>Cost</th><th>Latency</th><th>Result</th></tr></thead>
            <tbody>{calls.map((c) => (
              <tr key={c.id}>
                <td className="muted" style={{ whiteSpace: "nowrap" }}>{new Date(c.created_at).toLocaleString()}</td>
                <td>{c.task}</td>
                <td>{c.model}{c.fallback_used && <span className="badge processing" style={{ marginLeft: 6 }}>fallback</span>}</td>
                <td>{c.input_tokens} / {c.output_tokens}</td>
                <td>${(c.cost_usd ?? 0).toFixed(4)}</td>
                <td>{c.latency_ms} ms</td>
                <td>{c.success ? <span className="badge ready">ok</span> : <span className="badge failed" title={c.error ?? ""}>error</span>}</td>
              </tr>
            ))}</tbody>
          </table>
        )}
      </div>
    </main>
  );
}
