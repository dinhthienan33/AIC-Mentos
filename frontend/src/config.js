// Public FastAPI backend. Trailing slash stripped.
// Prefer REACT_APP_API_URL, then REACT_APP_BACKEND_ORIGIN.
// If both are empty, requests use same-origin paths (CRA dev proxy).
const configuredOrigin =
  process.env.REACT_APP_API_URL ||
  process.env.REACT_APP_BACKEND_ORIGIN ||
  '';
export const BACKEND_ORIGIN = String(configuredOrigin).replace(/\/$/, '');

export const API_BASE_URL = BACKEND_ORIGIN;

export const apiUrl = (path = '') => {
  const normalized = path.startsWith('/') ? path : `/${path}`;
  return `${API_BASE_URL}${normalized}`;
};
