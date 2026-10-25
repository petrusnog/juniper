// The only place that talks to the server. A 401 sends the person to the login page.

export async function api(path, params = {}) {
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== "") query.set(key, String(value));
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
