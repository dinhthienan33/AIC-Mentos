import { getSafeVideoUrl } from './videoUtils';
import { frameNumberToTimestamp, resolveFps } from './fpsUtils';

export const EMPTY_PLAYER_STATE = {
  open: false,
  url: '',
  t: 0,
  markers: [],
  keyframeRefs: [],
  videoId: '',
  frameRate: null,
  csvBaseName: '',
};

export const toKeyframeRef = (source = {}) => {
  const fps = resolveFps(source);
  const keyframeNum = source.keyframe_num ?? source.start_frame ?? null;
  let timestamp = source.timestamp ?? source.start_time;
  if (timestamp == null && keyframeNum != null && fps) {
    timestamp = frameNumberToTimestamp(keyframeNum, fps);
  }

  return {
    ...source,
    video_id: source.video_id || source.video_name,
    keyframe_num: keyframeNum,
    timestamp: timestamp ?? 0,
    fps: fps ?? source.fps,
    frame_rate: source.frame_rate ?? source.fps,
  };
};

export const createPlayerState = ({
  videoUrl,
  startSeconds = 0,
  keyframeRefs = [],
  videoId,
  frameRate,
  csvBaseName,
}) => {
  const refs = keyframeRefs.map(toKeyframeRef);
  const resolvedVideoId = videoId || refs[0]?.video_id || '';
  const start = Math.max(0, Math.floor(startSeconds || 0));
  const markers = refs.length
    ? refs.map((k) => Math.max(0, Math.floor(k.timestamp || 0)))
    : [start];

  return {
    open: true,
    url: getSafeVideoUrl(videoUrl),
    t: start,
    markers,
    keyframeRefs: refs,
    videoId: resolvedVideoId,
    frameRate: frameRate ?? resolveFps(...refs),
    csvBaseName: csvBaseName || resolvedVideoId,
  };
};
