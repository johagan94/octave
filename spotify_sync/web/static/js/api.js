// fetch wrapper. Always reads localStorage.api_key, unwraps the {data, error}
// envelope, throws a typed-ish error on non-2xx. Single source of HTTP truth.

const API_KEY_STORAGE = "spotify_sync_api_key";

export function getApiKey() {
  return localStorage.getItem(API_KEY_STORAGE) || "";
}

export function setApiKey(value) {
  if (value) localStorage.setItem(API_KEY_STORAGE, value);
  else       localStorage.removeItem(API_KEY_STORAGE);
}

class ApiError extends Error {
  constructor(code, message, status, details) {
    super(message || code);
    this.code = code;
    this.status = status;
    this.details = details;
  }
}

async function request(method, path, body) {
  const headers = { "Accept": "application/json" };
  const key = getApiKey();
  if (key) headers["X-API-Key"] = key;
  if (body !== undefined) headers["Content-Type"] = "application/json";

  let resp;
  try {
    resp = await fetch(path, {
      method,
      headers,
      body: body !== undefined ? JSON.stringify(body) : undefined,
    });
  } catch (e) {
    throw new ApiError("network_error", `Network error: ${e.message}`, 0);
  }

  let json = null;
  try { json = await resp.json(); } catch (_) { /* not all responses are JSON (eg SSE) */ }

  if (!resp.ok) {
    const code = json?.error?.code || `http_${resp.status}`;
    const msg  = json?.error?.message || resp.statusText || "Request failed";
    throw new ApiError(code, msg, resp.status, json?.error?.details);
  }
  return json?.data ?? null;
}

export const api = {
  get:  (path)        => request("GET",    path),
  post: (path, body)  => request("POST",   path, body ?? {}),
  put:  (path, body)  => request("PUT",    path, body ?? {}),
  del:  (path)        => request("DELETE", path),
};

export { ApiError };
