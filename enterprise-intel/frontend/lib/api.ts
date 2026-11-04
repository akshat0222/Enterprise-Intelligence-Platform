export const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export type Doc = {
  id: string;
  filename: string;
  file_type: string;
  size_bytes: number | null;
  status: "uploaded" | "processing" | "ready" | "failed";
  error_message: string | null;
  stats: { words?: number; chunks?: number; tables?: number; pages?: number } | null;
  created_at: string;
  duplicate?: boolean;
};

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

export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  let res = await raw(path, init);
  if (res.status === 401 && (await refreshSession())) res = await raw(path, init);
  if (res.status === 401) {
    setToken(null);
    if (typeof window !== "undefined") window.location.href = "/login";
  }
  return parse<T>(res);
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
