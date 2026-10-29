// Tiny fetch wrapper shared by both apps. No codegen in v1 (cut list).
export const API = "/api/v1";
const ACCESS = "gk_access", REFRESH = "gk_refresh";

export class ApiError extends Error {
  constructor(public status: number, public code: string) { super(code); }
}

const store = {
  get: (k: string) => { try { return localStorage.getItem(k); } catch { return null; } },
  set: (k: string, v: string | null) => { try { v === null ? localStorage.removeItem(k) : localStorage.setItem(k, v); } catch { /* private mode */ } },
};

export const accessToken = () => store.get(ACCESS);  // for the WebSocket query string
export const isSignedIn = () => !!store.get(ACCESS);
export const signOut = () => { store.set(ACCESS, null); store.set(REFRESH, null); };

export async function login(identifier: string, password: string): Promise<string> {
  const r = await fetch(`${API}/auth/login`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ identifier, password }) });
  if (!r.ok) throw new ApiError(r.status, (await r.json().catch(() => ({}))).error?.code ?? "LOGIN_FAILED");
  const j = await r.json();
  store.set(ACCESS, j.access_token); store.set(REFRESH, j.refresh_token);
  return j.role;
}

async function refresh(): Promise<boolean> {
  const rt = store.get(REFRESH);
  if (!rt) return false;
  const r = await fetch(`${API}/auth/refresh`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ refresh_token: rt }) });
  if (!r.ok) return false;
  const j = await r.json();
  store.set(ACCESS, j.access_token); store.set(REFRESH, j.refresh_token);
  return true;
}

export async function raw(path: string, init: RequestInit = {}, retry = true): Promise<Response> {
  const headers = new Headers(init.headers);
  const t = store.get(ACCESS);
  if (t) headers.set("Authorization", `Bearer ${t}`);
  const r = await fetch(`${API}${path}`, { ...init, headers });
  if (r.status === 401 && retry && (await refresh())) return raw(path, init, false);
  if (r.status === 401) signOut();
  return r;
}

export async function call<T = any>(path: string, init: RequestInit = {}): Promise<T> {
  const r = await raw(path, init);
  if (!r.ok) throw new ApiError(r.status, (await r.json().catch(() => ({}))).error?.code ?? `HTTP_${r.status}`);
  return r.status === 204 ? (undefined as T) : r.json();
}

export const json = (method: string, body: unknown): RequestInit => ({ method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
