export async function apiRequest(path, { token, body, headers, ...options } = {}) {
  const requestHeaders = new Headers(headers);
  if (token) requestHeaders.set('Authorization', `Bearer ${token}`);
  if (body !== undefined && !(body instanceof FormData)) {
    requestHeaders.set('Content-Type', 'application/json');
    body = JSON.stringify(body);
  }
  let response;
  try {
    response = await fetch(`/api${path}`, { ...options, headers: requestHeaders, body });
  } catch (error) {
    if (error.name === 'AbortError') throw error;
    throw new Error('Cannot reach DataGuard. Check your connection and that the API is running.');
  }
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = Array.isArray(data.detail)
      ? data.detail.map((d) => `${d.loc?.at(-1) || 'Input'}: ${d.msg}`).join(' ')
      : data.detail;
    throw Object.assign(
      new Error(
        data.error?.message || detail || `Request failed (${response.status}). Please try again.`,
      ),
      { status: response.status, code: data.error?.code },
    );
  }
  return data;
}

export function createApiService(token) {
  const request = (path, options) => apiRequest(path, { token, ...options });
  return {
    async listJobs(signal) {
      // The README supports offset pagination, but does not define search/filter parameters.
      // Read all pages so client-side filters and totals cover the owner's complete history.
      const items = [];
      let total = Infinity;
      while (items.length < total) {
        const page = await request(`/jobs?limit=20&offset=${items.length}`, { signal });
        items.push(...page.items);
        total = page.total;
        if (!page.items.length) break;
      }
      return items;
    },
    async listRules(signal) {
      const data = await request('/rule-sets', { signal });
      return Array.isArray(data) ? data : data.items;
    },
    createRule: (body) => request('/rule-sets', { method: 'POST', body }),
    createJob: (body) => request('/jobs', { method: 'POST', body }),
    getJob: (id, signal) => request(`/jobs/${encodeURIComponent(id)}`, { signal }),
    getReport: (id, signal) => request(`/jobs/${encodeURIComponent(id)}/report`, { signal }),
    async download(id, kind) {
      const result = await request(`/jobs/${encodeURIComponent(id)}/downloads/${kind}`);
      const target = new URL(result.url, window.location.origin);
      if (!['http:', 'https:'].includes(target.protocol))
        throw new Error('The server returned an unsupported download URL.');
      window.location.assign(target.href);
    },
  };
}
