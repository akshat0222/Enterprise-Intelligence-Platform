export const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:5500";

export type Doc = {
  id: string;
  filename: string;
  file_type: string;
  size_bytes: number | null;
  status: "uploaded" | "processing" | "ready" | "failed";
  error_message: string | null;
  stats: DocStats | null;
  created_at: string;
  processed_at?: string | null;
  duplicate?: boolean;
};

export type DocStats = {
  words?: number;
  tokens?: number;
  chunks?: number;
  tables?: number;
  pages?: number;
  sections?: number;
  images_removed?: number;
  flagged_chunks?: number;
  headings?: { level: number; text: string; page: number | null }[];
  keywords?: { word: string; count: number }[];
};

export type Chunk = {
  id: string;
  chunk_index: number;
  content: string;
  heading_path: string[] | null;
  page_start: number | null;
  page_end: number | null;
  content_type: string | null;
  token_count: number | null;
  flagged_injection: boolean;
};

export type Citation = {
  source_id: string;
  chunk_id: string;
  document_id: string;
  filename: string;
  page: number | null;
  section: string;
  quote: string;
};

export type Step = {
  step_no: number | null;
  tool_name: string | null;
  args: Record<string, unknown> | null;
  output_summary: Record<string, unknown> | null;
  status: string | null;
  error: string | null;
  latency_ms: number | null;
};

export type Conflict = { topic: string; sources: string[]; description: string };

export type Details = {
  models?: string[];
  input_tokens?: number;
  output_tokens?: number;
  cost_usd?: number;
  latency_ms?: number;
  llm_calls?: number;
  fallback_used?: boolean;
  issues?: string[];
  conflicts?: Conflict[];
  confidence?: string | null;
  flagged_sources?: string[];
};

export type Message = {
  id: string;
  role: "user" | "assistant";
  content: string;
  citations: Citation[] | null;
  route: string | null;
  status: string | null;
  groundedness_score: number | null;
  details: Details | null;
  created_at: string;
  steps: Step[];
};

export type Conversation = { id: string; title: string | null; active_document_ids: string[]; updated_at: string };
export type ConversationDetail = Conversation & { messages: Message[] };

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

// The access token lives in memory only; the httpOnly refresh cookie restores it after a page reload.
let accessToken: string | null = null;

export function setToken(token: string | null) {
  accessToken = token;
}

async function raw(path: string, init: RequestInit = {}): Promise<Response> {
  const headers = new Headers(init.headers);
  if (accessToken) headers.set("Authorization", `Bearer ${accessToken}`);
  return fetch(`${API_URL}${path}`, { ...init, headers, credentials: "include" });
}

async function parse<T>(res: Response): Promise<T> {
  if (res.status === 204) return undefined as T;
  const body = await res.json().catch(() => null);
  if (!res.ok) throw new ApiError(res.status, body?.error?.message ?? res.statusText);
  return body as T;
}

export async function refreshSession(): Promise<boolean> {
  const res = await raw("/auth/refresh", { method: "POST" });
  if (!res.ok) return false;
  setToken((await res.json()).access_token);
  return true;
}

/** fetch with the bearer token; on 401 it refreshes once, then sends the user to /login. */
export async function authFetch(path: string, init: RequestInit = {}): Promise<Response> {
  let res = await raw(path, init);
  if (res.status === 401 && (await refreshSession())) res = await raw(path, init);
  if (res.status === 401) {
    setToken(null);
    if (typeof window !== "undefined") window.location.href = "/login";
  }
  return res;
}

export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  return parse<T>(await authFetch(path, init));
}

export function jsonBody(data: unknown): RequestInit {
  return { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(data) };
}

export async function authenticate(mode: "login" | "register", email: string, password: string) {
  const creds = JSON.stringify({ email, password });
  const headers = { "Content-Type": "application/json" };
  if (mode === "register") {
    await parse(await raw("/auth/register", { method: "POST", headers, body: creds }));
  }
  const data = await parse<{ access_token: string }>(await raw("/auth/login", { method: "POST", headers, body: creds }));
  setToken(data.access_token);
}

export async function logout() {
  await raw("/auth/logout", { method: "POST" });
  setToken(null);
}

export type SSEHandler = (event: string, data: any) => void; // eslint-disable-line @typescript-eslint/no-explicit-any

/** POST a chat message and read the server-sent events (fetch + ReadableStream, so the auth header can be sent). */
export async function streamChat(
  conversationId: string,
  body: { content: string; document_ids: string[]; model?: string },
  onEvent: SSEHandler,
  signal?: AbortSignal,
) {
  const res = await authFetch(`/conversations/${conversationId}/messages`, { ...jsonBody(body), signal });
  if (!res.ok || !res.body) {
    const err = await res.json().catch(() => null);
    throw new ApiError(res.status, err?.error?.message ?? res.statusText);
  }
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let end: number;
    while ((end = buffer.indexOf("\n\n")) >= 0) {
      const block = buffer.slice(0, end);
      buffer = buffer.slice(end + 2);
      const event = /^event: (.*)$/m.exec(block)?.[1];
      const data = /^data: (.*)$/m.exec(block)?.[1];
      if (event && data) onEvent(event, JSON.parse(data));
    }
  }
}
