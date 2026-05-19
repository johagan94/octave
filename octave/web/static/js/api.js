// fetch wrapper. Unwraps the {data, error} envelope, throws a typed-ish
// error on non-2xx. Auth is handled by the browser via HTTP Basic Auth —
// on the first 401 the browser shows its native credential dialog, caches
// the credentials, and sends them with every subsequent request (including
// EventSource). No custom auth headers or localStorage management needed.

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
  try { json = await resp.json(); } catch (_) { /* SSE and other non-JSON responses */ }

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
