import React, { useState } from 'react';
import VideoPlayerModal from './VideoPlayModal';
import LazyImage from './LazyImage';
import { getSafeVideoUrl, isValidVideoUrl } from '../utils/videoUtils';
import { frameToMs, resolveFps } from '../utils/fpsUtils';
import DresShotButton from './DresShotButton';

const AllFramesView = ({ videoData, onOpenVideo, sortBy: externalSortBy, csvBaseName, rankVideoData }) => {
  const [sortByState, setSortByState] = useState('score'); // kept for backward compat, unused when external provided
  const sortBy = externalSortBy || sortByState;
  const [zoomedFrame, setZoomedFrame] = useState(null);
  const [player, setPlayer] = useState({
    open: false,
    url: '',
    t: 0,
    markers: [],
    keyframeRefs: [],
    videoId: '',
    frameRate: undefined,
  });

  if (!videoData || Object.keys(videoData).length === 0) {
    return <div className="status">No frames available</div>;
  }

  const openPlayer = (videoUrl, seconds, keyframes = [], videoId = '', frameRate) => {
    const safeVideoUrl = getSafeVideoUrl(videoUrl);
    const markers = Array.from(new Set((keyframes || [])
      .map(k => Math.max(0, Math.floor(k?.timestamp || 0)))
      .filter(n => Number.isFinite(n)))).sort((a, b) => a - b);

    setPlayer({
      open: true,
      url: safeVideoUrl,
      t: Math.max(0, Math.floor(seconds || 0)),
      markers,
      keyframeRefs: keyframes,
      videoId: String(videoId || ''),
      frameRate: typeof frameRate === 'number' ? frameRate : undefined,
    });
  };

  const collectFrames = (data) => {
    const frames = [];
    Object.values(data || {}).forEach((video) => {
      (video.keyframes || []).forEach((keyframe) => {
        frames.push({
          ...keyframe,
          video_id: video.video_id,
          video_url: video.video_url,
          group_id: video.group_id,
        });
      });
    });
    return frames;
  };

  const sortFrames = (frames) =>
    [...frames].sort((a, b) => {
      switch (sortBy) {
        case 'score':
          return b.confidence_score - a.confidence_score;
        case 'time':
          return (a.timestamp || 0) - (b.timestamp || 0);
        case 'video':
          return a.video_id.localeCompare(b.video_id);
        case 'frame':
        case 'name':
          return a.keyframe_num - b.keyframe_num;
        default:
          return 0;
      }
    });

  const frameKey = (frame) =>
    `${frame.video_id}|${frame.keyframe_num}|${frame.keyframe_id || ''}`;

  // Collect all frames from filtered videos (display set)
  const allFrames = collectFrames(videoData);
  const sortedFrames = sortFrames(allFrames);
  const scoreRankByKey = new Map();
  [...allFrames]
    .sort((a, b) => (b.confidence_score || 0) - (a.confidence_score || 0))
    .forEach((frame, index) => {
      const key = frameKey(frame);
      if (!scoreRankByKey.has(key)) scoreRankByKey.set(key, index + 1);
    });

  // Raw ranks from full unfiltered result set (before keyword filter)
  const rankSource = sortFrames(collectFrames(rankVideoData && Object.keys(rankVideoData).length ? rankVideoData : videoData));
  const rawRankByKey = new Map();
  rankSource.forEach((frame, i) => {
    const key = frameKey(frame);
    if (!rawRankByKey.has(key)) rawRankByKey.set(key, i + 1);
  });

  const buildUrlWithTime = (urlString, seconds) => {
    try {
      if (!urlString) return urlString;
      const url = new URL(urlString);
      const isYouTube = /youtube\.com|youtu\.be/.test(url.hostname);
      if (isYouTube) {
        url.searchParams.delete('t');
        url.searchParams.delete('start');
        const t = Math.max(0, Math.floor(seconds || 0));
        url.searchParams.set('t', `${t}s`);
        return url.toString();
      }
      const t = Math.max(0, Math.floor(seconds || 0));
      url.searchParams.set('t', `${t}`);
      return url.toString();
    } catch (_) {
      return urlString;
    }
  };

  const navigateToVideo = (videoUrl, seconds) => {
    const finalUrl = buildUrlWithTime(videoUrl, seconds);
    window.open(finalUrl, '_blank', 'noopener,noreferrer');
  };

  const downloadCSV = (videoId, frameIdx) => {
    const csvContent = `${videoId},${frameIdx}`;
    const blob = new Blob([csvContent], { type: 'text/csv;charset=utf-8;' });
    const link = document.createElement('a');
    const url = URL.createObjectURL(blob);
    link.setAttribute('href', url);
    const base = (csvBaseName && csvBaseName.trim().length > 0) ? csvBaseName.trim() : `${videoId}`;
    link.setAttribute('download', `${base}.csv`);
    link.style.visibility = 'hidden';
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
  };

  return (
    <>
      {/* Sort controls moved to toolbar */}

      <div className="grid">
        {sortedFrames.map((frame, i) => {
          const src = frame.image_url || 'data:image/gif;base64,R0lGODlhAQABAAAAACw=';
          const score = frame.confidence_score ? frame.confidence_score.toFixed(3) : '';
          const timestamp = frame.timestamp ? frame.timestamp.toFixed(1) : 'N/A';
          const video = videoData[frame.video_id] || {};
          const fps = resolveFps(video, frame);
          const ms = frameToMs(frame.keyframe_num, fps);
          const rank = rawRankByKey.get(frameKey(frame)) ?? (i + 1);
          const scoreRank = scoreRankByKey.get(frameKey(frame)) ?? rank;
          return (
            <div className="card" key={`frame-${i}`}>
              <span className="result-rank" aria-label={`Rank ${rank}`}>
                {rank}
              </span>
              <LazyImage
                className="thumb keyframe-thumb"
                src={src}
                alt={`${frame.video_id} - Frame ${frame.keyframe_num}`}
                fetchPriority={i < 4 ? 'high' : 'low'}
                onClick={() => setZoomedFrame(frame)}
              />
              <div className="meta">
                <div className="frame-header">
                  <div title={frame.keyframe_id}>
                    Frame {frame.keyframe_num}
                    {score && <span className="score-display">{score}</span>}
                  </div>
                  <div className="video-info">
                    <span className="video-name">{frame.video_id}</span>
                  </div>
                </div>
                <div className="frame-timestamp">
                  <span className="timestamp">{timestamp}s</span>
                  <span className="timestamp ms-tag">{ms == null ? 'fps?' : `${ms} ms`}</span>
                </div>
                <div className="frame-actions">
                  <button
                    className="youtube-btn"
                    onClick={(e) => {
                      e.stopPropagation();
                      openPlayer(
                        frame.video_url,
                        frame.timestamp,
                        video.keyframes || [],
                        frame.video_id,
                        fps
                      );
                    }}
                  >
                    {isValidVideoUrl(frame.video_url) ? '📺 YouTube' : '📺 YouTube'}
                  </button>
                  <button
                    className="csv-btn"
                    onClick={(e) => {
                      e.stopPropagation();
                      downloadCSV(frame.video_id, frame.keyframe_num);
                    }}
                  >
                    📊 CSV
                  </button>
                </div>
                <DresShotButton
                  videoId={frame.video_id}
                  frame={frame.keyframe_num}
                  fps={fps}
                  rank={frame.result_rank ?? scoreRank}
                />
              </div>

            </div>
          );
        })}
      </div>

      {/* Zoom Frame Modal */}
      {zoomedFrame && (
        <div className="zoom-frame-overlay" onClick={() => setZoomedFrame(null)}>
          <div className="zoom-frame-container" onClick={(e) => e.stopPropagation()}>
            <div className="zoom-frame-header">
              <div className="zoom-frame-title">
                Frame {zoomedFrame.keyframe_num} - {zoomedFrame.video_id}
              </div>
              <button
                className="zoom-frame-close"
                onClick={() => setZoomedFrame(null)}
              >
                ×
              </button>
            </div>
            <div className="zoom-frame-content">
              <LazyImage
                wrapperClassName="zoom-frame-wrap"
                className="zoom-frame-image"
                src={zoomedFrame.image_url || 'data:image/gif;base64,R0lGODlhAQABAAAAACw='}
                alt={`${zoomedFrame.video_id} - Frame ${zoomedFrame.keyframe_num}`}
                eager
              />
              <div className="zoom-frame-info">
                <div className="zoom-frame-details">
                  <span className="zoom-frame-score">
                    Score: {zoomedFrame.confidence_score ? zoomedFrame.confidence_score.toFixed(3) : 'N/A'}
                  </span>
                  <span className="zoom-frame-timestamp">
                    Time: {zoomedFrame.timestamp ? `${zoomedFrame.timestamp.toFixed(1)}s` : 'N/A'}
                    {' · '}
                    {frameToMs(zoomedFrame.keyframe_num, resolveFps(videoData[zoomedFrame.video_id], zoomedFrame)) == null
                      ? 'thiếu fps'
                      : `${frameToMs(zoomedFrame.keyframe_num, resolveFps(videoData[zoomedFrame.video_id], zoomedFrame))} ms`}
                  </span>
                </div>
                <div className="zoom-frame-actions">
                  <button
                    className="youtube-btn zoom-youtube-btn"
                    onClick={() => {
                      const video = videoData[zoomedFrame.video_id] || {};
                      openPlayer(
                        zoomedFrame.video_url,
                        zoomedFrame.timestamp,
                        video.keyframes || [],
                        zoomedFrame.video_id,
                        video.frame_rate ?? video.fps
                      );
                    }}

                  >
                    {isValidVideoUrl(zoomedFrame.video_url) ? '📺 Watch' : '📺 YouTube'}
                  </button>
                  <button
                    className="csv-btn zoom-csv-btn"
                    onClick={() => downloadCSV(zoomedFrame.video_id, zoomedFrame.keyframe_num)}
                  >
                    📊 Download CSV
                  </button>
                  <DresShotButton
                    videoId={zoomedFrame.video_id}
                    frame={zoomedFrame.keyframe_num}
                    fps={resolveFps(videoData[zoomedFrame.video_id], zoomedFrame)}
                    rank={zoomedFrame.result_rank ?? scoreRankByKey.get(frameKey(zoomedFrame))}
                  />
                </div>
                {videoData[zoomedFrame.video_id] && (
                  <div style={{ marginTop: '10px' }}>
                    <div style={{ color: '#a2b0c6', marginBottom: '6px' }}>All frames in this video</div>
                    <div style={{ display: 'flex', overflowX: 'auto', gap: '8px', paddingBottom: '6px' }}>
                      {videoData[zoomedFrame.video_id].keyframes.map((kf, idx) => {
                        const kfSrc = kf.image_url || 'data:image/gif;base64,R0lGODlhAQABAAAAACw=';
                        const isActive = kf.keyframe_num === zoomedFrame.keyframe_num;
                        return (
                          <div
                            key={`strip-${zoomedFrame.video_id}-${idx}`}
                            style={{ minWidth: '120px', border: isActive ? '2px solid #4da3ff' : '1px solid #2b3b52', borderRadius: '6px', padding: '2px' }}
                          >
                            <LazyImage
                              wrapperClassName="strip-thumb-wrap"
                              className="strip-thumb"
                              src={kfSrc}
                              alt={`${zoomedFrame.video_id} - Frame ${kf.keyframe_num}`}
                              onClick={() => setZoomedFrame({ ...kf, video_url: zoomedFrame.video_url })}
                            />
                          </div>
                        );
                      })}
                    </div>
                  </div>
                )}
              </div>
            </div>
          </div>
        </div>
      )}
      {player.open && (
        <VideoPlayerModal
          videoUrl={player.url}
          startSeconds={player.t}
          markers={player.markers}
          keyframeRefs={player.keyframeRefs}
          videoId={player.videoId}
          frameRate={player.frameRate}
          csvBaseName={csvBaseName}
          onClose={() =>
            setPlayer({
              open: false,
              url: '',
              t: 0,
              markers: [],
              keyframeRefs: [],
              videoId: '',
              frameRate: undefined,
            })
          }
        />
      )}
    </>
  );
};

export default AllFramesView;
