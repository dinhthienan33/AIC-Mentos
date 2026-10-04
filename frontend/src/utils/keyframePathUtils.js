import { BACKEND_ORIGIN } from '../config';

/**
 * Parse backend keyframe path (e.g. keyframes/L21_V001/022444.jpg) into display fields.
 */
export const parseKeyframePath = (path) => {
  if (!path || typeof path !== 'string') return null;

  const match = path.match(/^keyframes\/([^/]+)\/(\d+)\.jpg$/i);
  if (!match) {
    const imageUrl = path.startsWith('http')
      ? path
      : `${BACKEND_ORIGIN}/${path.replace(/^\//, '')}`;
    return { image_path: path, image_url: imageUrl };
  }

  const videoId = match[1];
  const keyframeNum = parseInt(match[2], 10);
  const groupId = videoId.split('_')[0];

  return {
    video_id: videoId,
    group_id: groupId,
    keyframe_num: keyframeNum,
    image_path: path,
    image_url: `${BACKEND_ORIGIN}/frames/${videoId}/${keyframeNum}.jpg`,
  };
};

/**
 * Normalize filter-search OD results (string paths or legacy objects).
 */
export const normalizeOdResults = (data) => {
  const raw = data?.results || [];
  const detectedObjects = data?.filters_applied?.od_text || [];
  const fpsMap = data?.fps || {};

  return raw
    .map((item) => {
      if (typeof item === 'string') {
        const parsed = parseKeyframePath(item);
        if (!parsed) return null;
        return {
          ...parsed,
          fps: fpsMap[parsed.video_id],
          detected_objects: detectedObjects,
        };
      }

      const parsedFromPath = item.image_path ? parseKeyframePath(item.image_path) : null;
      return {
        ...parsedFromPath,
        ...item,
        fps: item.fps ?? fpsMap[item.video_id] ?? fpsMap[parsedFromPath?.video_id],
        image_url:
          item.image_url ||
          item.thumbnail_url ||
          parsedFromPath?.image_url,
        detected_objects: item.object_names || detectedObjects,
      };
    })
    .filter(Boolean);
};
