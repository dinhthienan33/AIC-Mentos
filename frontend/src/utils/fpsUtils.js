/**
 * Frame/time helpers. FPS comes from backend responses (fps / frame_rate fields).
 */

export const resolveFps = (...sources) => {
  for (const source of sources) {
    if (typeof source === 'number' && Number.isFinite(source) && source > 0) {
      return source;
    }
    if (source && typeof source === 'object') {
      const fps = source.fps ?? source.frame_rate;
      if (typeof fps === 'number' && Number.isFinite(fps) && fps > 0) {
        return fps;
      }
    }
  }
  return null;
};

export const timestampToFrameNumber = (timestamp, fps) => {
  return Math.floor(timestamp * fps);
};

export const frameNumberToTimestamp = (frameNumber, fps) => {
  return frameNumber / fps;
};

/** Milliseconds for DRES. Returns null when fps is missing — callers must not guess. */
export const frameToMs = (frameNumber, fps) => {
  const frame = Number(frameNumber);
  const rate = Number(fps);
  if (!Number.isFinite(frame) || !Number.isFinite(rate) || rate <= 0) {
    return null;
  }
  return Math.round((frame / rate) * 1000);
};
