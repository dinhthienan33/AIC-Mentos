import { apiUrl } from '../config';

const errorText = (status, data) => {
  if (typeof data?.detail === 'string') return data.detail;
  if (typeof data?.description === 'string' && data.description) return data.description;
  if (data?.message) return String(data.message);
  return `DRES HTTP ${status}`;
};

export async function dresCall({ baseUrl, method, path, query, body }) {
  let response;
  try {
    response = await fetch(apiUrl('/dres-proxy'), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        base_url: baseUrl,
        method,
        path,
        query: query || {},
        body: body ?? null,
      }),
    });
  } catch (err) {
    const wrapped = new Error('Không gọi được API Mentos (/dres-proxy).');
    wrapped.cause = err;
    throw wrapped;
  }

  const data = await response.json().catch(() => ({}));
  if (response.status === 404) {
    throw new Error('Backend chưa có /dres-proxy. Cần chạy API Mentos mới.');
  }
  return { status: response.status, data };
}

export async function dresLogin(baseUrl, username, password) {
  const { status, data } = await dresCall({
    baseUrl,
    method: 'POST',
    path: '/api/v2/login',
    body: { username, password },
  });
  if (status < 200 || status >= 300) {
    const err = new Error(errorText(status, data));
    err.status = status;
    throw err;
  }
  const sessionId = data?.sessionId || data?.session;
  if (!sessionId) {
    throw new Error('Login DRES không trả sessionId.');
  }
  return sessionId;
}

export function evaluationListFrom(data) {
  if (Array.isArray(data)) return data;
  if (Array.isArray(data?.evaluations)) return data.evaluations;
  if (Array.isArray(data?.results)) return data.results;
  return [];
}

export function activeEvaluations(data) {
  return evaluationListFrom(data).filter(
    (item) => String(item?.status || '').toUpperCase() === 'ACTIVE' && item?.id,
  );
}

export function pickActiveEvaluation(data) {
  return activeEvaluations(data)[0] || null;
}

export function taskTypeFromEvaluationName(name) {
  const label = String(name || '').toLowerCase();
  if (label.includes('trake')) return 'trake';
  if (label.includes('video') && label.includes('kis')) return 'video-kis';
  if (label.includes('q&a') || label.includes('q & a') || /\bqa\b/.test(label)) return 'qa';
  if (label.includes('textual') || label.includes('kis')) return 'textual-kis';
  return null;
}

export async function dresListEvaluations(baseUrl, sessionId) {
  return dresCall({
    baseUrl,
    method: 'GET',
    path: '/api/v2/client/evaluation/list',
    query: { session: sessionId },
  });
}

export async function dresSubmit(baseUrl, sessionId, evaluationId, body) {
  return dresCall({
    baseUrl,
    method: 'POST',
    path: `/api/v2/submit/${encodeURIComponent(evaluationId)}`,
    query: { session: sessionId },
    body,
  });
}

export { errorText };
