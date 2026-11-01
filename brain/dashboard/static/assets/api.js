// The only place that talks to the server. A 401 sends the person to the login page.

export async function api(path, params = {}) {
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (Array.isArray(value)) {
      for (const item of value) query.append(key, String(item));
    } else if (value !== undefined && value !== null && value !== "") {
      query.set(key, String(value));
    }
  }
  const suffix = query.toString() ? `?${query}` : "";
  const response = await fetch(`/api${path}${suffix}`);
  if (response.status === 401) {
    window.location.href = "/login";
    throw new Error("sessão expirada");
  }
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = new Error(body.error || `erro ${response.status}`);
    error.status = response.status;
    throw error;
  }
  return body;
}

let csrf = null;

// The only writes the dashboard makes (users and credentials, ADR-027). The parameters ride in
// the query (none is a secret) and the anti-CSRF token of the session in a header.
export async function post(path, params = {}) {
  if (!csrf) csrf = (await api("/me")).csrf;
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== "") query.set(key, String(value));
  }
  const suffix = query.toString() ? `?${query}` : "";
  const response = await fetch(`/api${path}${suffix}`, { method: "POST", headers: { "X-CSRF-Token": csrf } });
  if (response.status === 401) {
    window.location.href = "/login";
    throw new Error("sessão expirada");
  }
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = new Error(body.error || `erro ${response.status}`);
    error.status = response.status;
    throw error;
  }
  return body;
}
