const TOKEN_KEY = "herwiego.token";

export const getToken = () => {
  try {
    return localStorage.getItem(TOKEN_KEY) || "";
  } catch {
    return "";
  }
};
export const setToken = (t) => {
  try {
    t ? localStorage.setItem(TOKEN_KEY, t) : localStorage.removeItem(TOKEN_KEY);
  } catch {
    /* storage unavailable: the session just will not survive a reload */
  }
};

let currentLang = "en";
export const setApiLang = (code) => { currentLang = code; };
export const getApiLang = () => currentLang;

export class ApiError extends Error {
  constructor(message, status, retryAfter, kind) {
    super(message);
    this.status = status;
    this.retryAfter = retryAfter || null;
    this.kind = kind || null;
  }
}

export async function api(path, { method = "GET", body } = {}) {
  const headers = { "Content-Type": "application/json", "X-Lang": currentLang };
  const token = getToken();
  if (token) headers.Authorization = `Bearer ${token}`;
  let res;
  try {
    res = await fetch(path, { method, headers, body: body ? JSON.stringify(body) : undefined });
  } catch {
    throw new ApiError("Cannot reach the HerwieGO server. Check that the backend is running.", 0);
  }
  let data = null;
  try {
    data = await res.json();
  } catch {
    /* empty body */
  }
  if (!res.ok) {
    const detail = typeof data?.detail === "string" ? data.detail : "Something went wrong. Try again.";
    throw new ApiError(detail, res.status, data?.retry_after, data?.kind);
  }
  return data;
}

/**
 * POST that answers as Server-Sent Events (`data: {json}` blocks): calls onEvent for each event as it
 * arrives. Used by study-space turns, where each finished stage (intent, sources, draft, final answer,
 * figure) is pushed the moment it is ready. Abort with `signal`.
 */
export function streamPost(path, body, onEvent, signal) {
  return streamRequest("POST", path, body, onEvent, signal);
}

/** Server-Sent Events over fetch (POST to start a turn, GET to reconnect to one). Resolves false on 204 (nothing running). */
export async function streamRequest(method, path, body, onEvent, signal) {
  const headers = { "X-Lang": currentLang, Accept: "text/event-stream" };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  const token = getToken();
  if (token) headers.Authorization = `Bearer ${token}`;
  let res;
  try {
    res = await fetch(path, { method, headers, body: body === undefined ? undefined : JSON.stringify(body), signal });
  } catch (e) {
    if (e?.name === "AbortError") throw e;
    throw new ApiError("Cannot reach the HerwieGO server. Check that the backend is running.", 0);
  }
  if (!res.ok) {
    let data = null;
    try { data = await res.json(); } catch { /* empty body */ }
    throw new ApiError(typeof data?.detail === "string" ? data.detail : "Something went wrong. Try again.", res.status, data?.retry_after, data?.kind);
  }
  if (res.status === 204) return false;
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  const flush = (block) => {
    for (const line of block.split("\n")) {
      if (!line.startsWith("data: ")) continue;
      let event = null;
      try { event = JSON.parse(line.slice(6)); } catch { /* a malformed line is skipped */ }
      if (event) onEvent(event);
    }
  };
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let cut;
    while ((cut = buffer.indexOf("\n\n")) >= 0) {
      flush(buffer.slice(0, cut));
      buffer = buffer.slice(cut + 2);
    }
  }
  if (buffer.trim()) flush(buffer);
  return true;
}

export const wsUrl = (code) => {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  return `${proto}://${location.host}/ws/rooms/${encodeURIComponent(code)}?token=${encodeURIComponent(getToken())}&lang=${currentLang}`;
};

/** The pocket-tree space needs no login: the token alone (minted for a room by /api/rooms/{code}/phone-tree) identifies it. */
export const phoneTreeWsUrl = (token) => {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  return `${proto}://${location.host}/ws/phone-tree/${encodeURIComponent(token)}?lang=${currentLang}`;
};

/** URL of an uploaded image resource, authenticated by a token in the query string (an <img> cannot send headers). */
export function materialRawUrl(id, scope = "me") {
  return `/api/materials/${id}/raw?scope=${encodeURIComponent(scope)}&token=${encodeURIComponent(getToken())}`;
}

/** Raw-body upload (no multipart): the file name travels in X-Filename. */
export async function uploadMaterial(scope, body, filename, title = "") {
  const headers = { "X-Filename": encodeURIComponent(filename), "X-Lang": currentLang };
  const token = getToken();
  if (token) headers.Authorization = `Bearer ${token}`;
  let res;
  try {
    res = await fetch(`/api/materials/file?scope=${encodeURIComponent(scope)}&title=${encodeURIComponent(title)}`, { method: "POST", headers, body });
  } catch {
    throw new ApiError("Cannot reach the HerwieGO server. Check that the backend is running.", 0);
  }
  let data = null;
  try {
    data = await res.json();
  } catch {
    /* empty body */
  }
  if (!res.ok) throw new ApiError(typeof data?.detail === "string" ? data.detail : "The upload failed. Try again.", res.status);
  return data;
}
