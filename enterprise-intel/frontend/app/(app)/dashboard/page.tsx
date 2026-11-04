"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";

type Overview = {
  documents: number; chunks: number; conversations: number; messages: number; llm_calls: number; tokens: number;
  cost_usd: number; avg_latency_ms: number; error_rate: number; fallback_calls: number;
};
type Usage = { series: { day: string; provider: string; tokens: number; calls: number }[] };
type Activity = { id: number; action: string | null; entity_type: string | null; level: string; created_at: string; details: Record<string, unknown> | null };

const COLORS: Record<string, string> = { anthropic: "var(--accent)", gemini: "var(--warn)" };

export default function DashboardPage() {
  const [overview, setOverview] = useState<Overview | null>(null);
  const [usage, setUsage] = useState<Usage | null>(null);
  const [activity, setActivity] = useState<Activity[] | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    Promise.all([
      api<Overview>("/stats/overview"),
      api<Usage>("/stats/usage?range=7d"),
      api<Activity[]>("/logs/activity?limit=10"),
    ]).then(([o, u, a]) => { setOverview(o); setUsage(u); setActivity(a); })
      .catch((e) => setError(e instanceof Error ? e.message : "Failed to load dashboard"));
  }, []);

  if (error) return <main><p className="error">{error}</p></main>;
  if (!overview || !usage || !activity) return <main><p className="muted">Loading…</p></main>;

  const kpis: [string, string][] = [
    ["Documents", String(overview.documents)],
    ["Chunks", overview.chunks.toLocaleString()],
    ["Conversations", String(overview.conversations)],
    ["Messages", String(overview.messages)],
    ["Tokens used", overview.tokens.toLocaleString()],
    ["Est. cost", `$${overview.cost_usd.toFixed(3)}`],
    ["Avg LLM latency", `${(overview.avg_latency_ms / 1000).toFixed(1)} s`],
    ["Error rate", `${(overview.error_rate * 100).toFixed(1)}%`],
  ];

  const providers = Array.from(new Set(usage.series.map((s) => s.provider)));
  const byProvider = providers.map((p) => ({
    provider: p,
    tokens: usage.series.filter((s) => s.provider === p).reduce((a, s) => a + s.tokens, 0),
    calls: usage.series.filter((s) => s.provider === p).reduce((a, s) => a + s.calls, 0),
  }));

  return (
    <main>
      <div className="row"><h1>Dashboard</h1></div>
      <div className="kpis">
        {kpis.map(([label, value]) => (
          <div className="card kpi" key={label}><div className="muted">{label}</div><div className="kpi-value">{value}</div></div>
        ))}
      </div>

      <div className="two-col">
        <div className="card">
          <h3>Tokens per day (7 days)</h3>
          <UsageChart series={usage.series} providers={providers} />
        </div>
        <div className="card">
          <h3>Providers</h3>
          {byProvider.length === 0 ? <p className="muted">No LLM calls yet.</p> : (
            <table>
              <thead><tr><th>Provider</th><th>Calls</th><th>Tokens</th></tr></thead>
              <tbody>{byProvider.map((p) => <tr key={p.provider}><td>{p.provider}</td><td>{p.calls}</td><td>{p.tokens.toLocaleString()}</td></tr>)}</tbody>
            </table>
          )}
          <p className="muted">Calls served by a fallback provider: <strong>{overview.fallback_calls}</strong></p>
        </div>
      </div>

      <div className="card" style={{ marginTop: 16 }}>
        <h3>Recent activity</h3>
        {activity.length === 0 ? <p className="muted">No activity yet.</p> : (
          <table>
            <tbody>
              {activity.map((a) => (
                <tr key={a.id}>
                  <td style={{ width: 170 }} className="muted">{new Date(a.created_at).toLocaleString()}</td>
                  <td>{a.action}</td>
                  <td className="muted">{a.entity_type}</td>
                  <td><span className={`badge ${a.level === "info" ? "ready" : a.level === "warning" ? "processing" : "failed"}`}>{a.level}</span></td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </main>
  );
}

function UsageChart({ series, providers }: { series: Usage["series"]; providers: string[] }) {
  const days = Array.from(new Set(series.map((s) => s.day))).sort();
  if (!days.length) return <p className="muted">No usage yet.</p>;
  const totals = days.map((d) => series.filter((s) => s.day === d).reduce((a, s) => a + s.tokens, 0));
  const max = Math.max(...totals, 1);
  const W = 480, H = 180, pad = 28, bw = Math.min(48, (W - pad * 2) / days.length - 8);
  return (
    <svg viewBox={`0 0 ${W} ${H + 24}`} role="img" aria-label="Tokens per day" style={{ width: "100%" }}>
      {days.map((d, i) => {
        const x = pad + i * ((W - pad * 2) / days.length) + 4;
        let y = H;
        return (
          <g key={d}>
            {providers.map((p) => {
              const tokens = series.find((s) => s.day === d && s.provider === p)?.tokens ?? 0;
              const h = (tokens / max) * (H - 10);
              y -= h;
              return h > 0 ? <rect key={p} x={x} y={y} width={bw} height={h} fill={COLORS[p] ?? "var(--muted)"} rx={2}><title>{`${p}: ${tokens.toLocaleString()} tokens`}</title></rect> : null;
            })}
            <text x={x + bw / 2} y={H + 14} textAnchor="middle" fontSize="10" fill="var(--muted)">{d.slice(5)}</text>
            <text x={x + bw / 2} y={H - (totals[i] / max) * (H - 10) - 4} textAnchor="middle" fontSize="10" fill="var(--text)">{totals[i] >= 1000 ? `${(totals[i] / 1000).toFixed(1)}k` : totals[i]}</text>
          </g>
        );
      })}
      <line x1={pad} x2={W - pad} y1={H} y2={H} stroke="var(--border)" />
    </svg>
  );
}
