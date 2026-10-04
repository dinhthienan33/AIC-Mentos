export const TAB_PATHS = {
  visual: '/',
  asr: '/asr-search',
  od: '/od-search',
  ocr: '/ocr-search',
  cctv: '/cctv',
  shot: '/shot-ocr',
  voice: '/voice-asr',
};

const PATH_TO_TAB = {
  '/': 'visual',
  '/visual': 'visual',
  '/visual-search': 'visual',
  '/asr': 'asr',
  '/asr-search': 'asr',
  '/od': 'od',
  '/od-search': 'od',
  '/ocr': 'ocr',
  '/ocr-search': 'ocr',
  '/cctv': 'cctv',
  '/shot-ocr': 'shot',
  '/voice': 'voice',
  '/voice-asr': 'voice',
};

export const normalizePath = (pathname = '') => {
  const trimmed = pathname.replace(/\/+$/, '');
  return trimmed || '/';
};

export const tabFromPath = (pathname) => PATH_TO_TAB[normalizePath(pathname)] || 'visual';

export const pathForTab = (tab) => TAB_PATHS[tab] || '/';
