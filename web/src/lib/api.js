async function get(path, params) {
  const response = await fetch(`${path}?${new URLSearchParams(params)}`);
  const data = await response.json().catch(() => ({ error: response.statusText }));
  if (!response.ok) throw Object.assign(new Error(data.error || response.statusText), data);
  return data;
}

export const stats = () => get('/api/stats', {});

export const search = ({ q, octave, transpose, limit = 10, context = 8 }) =>
  get('/api/search', { q, octave: +octave, transpose: +transpose, limit, context });

export const context = ({ doc, pos, len, context = 8 }) =>
  get('/api/context', { doc, pos, len, context });
