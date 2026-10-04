/**
 * Dev-only proxy: browser calls same origin (port 3000), CRA forwards to the API.
 * Useful over SSH so you only need: ssh -L 3000:127.0.0.1:3000 ...
 *
 * Target (first non-empty): BACKEND_URL, REACT_APP_API_URL, REACT_APP_BACKEND_ORIGIN,
 * then http://localhost:8000.
 */
const { createProxyMiddleware } = require('http-proxy-middleware');

const BACKEND =
  process.env.BACKEND_URL ||
  process.env.REACT_APP_API_URL ||
  process.env.REACT_APP_BACKEND_ORIGIN ||
  'http://localhost:8000';

const apiPaths = [
  '/search',
  '/filter-search',
  '/asr-search',
  '/ocr-search',
  '/asr-transcribe',
  '/catalog',
  '/health',
  '/models',
  '/warmup',
  '/dres-proxy',
  '/cctv',
  '/ocr-image',
  '/create-thumbnails',
  '/docs',
  '/openapi.json',
];

module.exports = function setupProxy(app) {
  apiPaths.forEach((path) => {
    app.use(
      path,
      createProxyMiddleware({
        target: BACKEND,
        changeOrigin: true,
      })
    );
  });
};
