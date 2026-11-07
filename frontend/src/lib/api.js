const TOKEN_KEY = "ednova.token";

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
    throw new ApiError("Cannot reach the EdNova server. Check that the backend is running.", 0);
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

export const wsUrl = (code) => {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  return `${proto}://${location.host}/ws/rooms/${encodeURIComponent(code)}?token=${encodeURIComponent(getToken())}&lang=${currentLang}`;
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
    throw new ApiError("Cannot reach the EdNova server. Check that the backend is running.", 0);
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
