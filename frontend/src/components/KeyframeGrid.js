import React, { useEffect, useMemo, useState } from 'react';
import LazyImage from './LazyImage';
import VideoPlayerModal from './VideoPlayModal';
import { getSafeVideoUrl, isValidVideoUrl } from '../utils/videoUtils';
import { downloadKeyframesCsv, formatKeyframeCsvLine } from '../utils/csvUtils';
import { frameToMs, resolveFps } from '../utils/fpsUtils';
import DresShotButton from './DresShotButton';

const KeyframeGrid = ({ selectedVideoId, videoData, onBackToVideos, onOpenVideo, sortBy: externalSortBy, csvBaseName }) => {
  const [zoomedFrame, setZoomedFrame] = useState(null);
  const [player, setPlayer] = useState({ open: false, url: '', t: 0, markers: [] });
  const [selectedFrameNums, setSelectedFrameNums] = useState(new Set());

  const sortBy = externalSortBy || 'score';
  const video = selectedVideoId ? videoData[selectedVideoId] : null;
  const keyframes = video?.keyframes || [];

  const sortedKeyframes = useMemo(() => {
    return [...keyframes].sort((a, b) => {
      switch (sortBy) {
        case 'name':
          return a.keyframe_num - b.keyframe_num;
        case 'score':
          return b.confidence_score - a.confidence_score;
        case 'time':
          return (a.timestamp || 0) - (b.timestamp || 0);
        default:
          return 0;
      }
    });
  }, [keyframes, sortBy]);

  const scoreRankByFrame = useMemo(() => {
    const ranked = [...keyframes].sort(
      (a, b) => (b.confidence_score || 0) - (a.confidence_score || 0),
    );
    const ranks = new Map();
    ranked.forEach((keyframe, index) => {
      if (!ranks.has(keyframe.keyframe_num)) ranks.set(keyframe.keyframe_num, index + 1);
    });
    return ranks;
  }, [keyframes]);

  useEffect(() => {
    setSelectedFrameNums(new Set());
    setZoomedFrame(null);
  }, [selectedVideoId]);

  if (!selectedVideoId || !video) {
    return <div className="status">No video selected</div>;
  }

  const openPlayer = (videoUrl, seconds, keyframesList = []) => {
    const safeVideoUrl = getSafeVideoUrl(videoUrl);
    const keyframeRefs = (keyframesList || [])
      .map((k) => ({
        sec: Math.max(0, Math.floor(k?.timestamp || 0)),
        frameIdx: k?.keyframe_num,
      }))
      .filter((x) => Number.isFinite(x.sec) && Number.isFinite(x.frameIdx))
      .sort((a, b) => a.sec - b.sec);
    setPlayer({
      open: true,
      url: safeVideoUrl,
      t: Math.max(0, Math.floor(seconds || 0)),
      markers: keyframeRefs.map((x) => x.sec),
      keyframeRefs: keyframesList,
    });
  };

  const downloadOneCSV = (videoId, frameIdx) => {
    downloadKeyframesCsv(
      [formatKeyframeCsvLine(videoId, frameIdx)],
      csvBaseName && csvBaseName.trim() ? csvBaseName.trim() : videoId
    );
  };

  const toggleFrameSelection = (keyframeNum) => {
    setSelectedFrameNums((prev) => {
      const next = new Set(prev);
      if (next.has(keyframeNum)) {
        next.delete(keyframeNum);
      } else {
        next.add(keyframeNum);
      }
      return next;
    });
  };

  const selectAllFrames = () => {
    setSelectedFrameNums(new Set(sortedKeyframes.map((kf) => kf.keyframe_num)));
  };

  const clearSelection = () => {
    setSelectedFrameNums(new Set());
  };

  const exportSelectedCsv = () => {
    const lines = sortedKeyframes
      .filter((kf) => selectedFrameNums.has(kf.keyframe_num))
      .map((kf) => formatKeyframeCsvLine(video.video_id, kf.keyframe_num));

    if (!lines.length) return;

    const base =
      csvBaseName && csvBaseName.trim()
        ? csvBaseName.trim()
        : `${video.video_id}-selected`;
    downloadKeyframesCsv(lines, base);
  };

  const allSelected =
    sortedKeyframes.length > 0 && selectedFrameNums.size === sortedKeyframes.length;

  return (
    <>
      <button className="back-button" onClick={onBackToVideos}>
        ← Back to Videos
      </button>

      <div className="keyframes-header">
        <div>
          <h2 style={{ margin: 0, color: '#eaf0f6' }}>
            {selectedVideoId} ({video.group_id})
          </h2>
          <p style={{ margin: '4px 0 0 0', color: '#a2b0c6', fontSize: '14px' }}>
            {video.keyframes.length} keyframes found
            {selectedFrameNums.size > 0 && (
              <span className="selection-count"> · {selectedFrameNums.size} selected</span>
            )}
          </p>
        </div>
        <div className="keyframes-header-actions">
          <button type="button" className="selection-btn" onClick={selectAllFrames}>
            {allSelected ? 'All Selected' : 'Select All'}
          </button>
          <button
            type="button"
            className="selection-btn"
            onClick={clearSelection}
            disabled={selectedFrameNums.size === 0}
          >
            Clear
          </button>
          <button
            type="button"
            className="selection-btn selection-btn-primary"
            onClick={exportSelectedCsv}
            disabled={selectedFrameNums.size === 0}
          >
            Export CSV ({selectedFrameNums.size})
          </button>
        </div>
      </div>

      <div className="youtube-section">
        <button
          className="video-link-btn youtube-top-btn"
          onClick={() => openPlayer(video.video_url, zoomedFrame?.timestamp ?? 0, video.keyframes)}
        >
          {isValidVideoUrl(video.video_url) ? '📺 Watch' : '📺 YouTube'}
        </button>
      </div>

      <div className="grid">
        {sortedKeyframes.map((keyframe, i) => {
          const src = keyframe.image_url || 'data:image/gif;base64,R0lGODlhAQABAAAAACw=';
          const score = keyframe.confidence_score ? keyframe.confidence_score.toFixed(3) : '';
          const timestamp = keyframe.timestamp ? keyframe.timestamp.toFixed(1) : 'N/A';
          const isSelected = selectedFrameNums.has(keyframe.keyframe_num);
          const fps = resolveFps(video, keyframe);
          const ms = frameToMs(keyframe.keyframe_num, fps);

          return (
            <div
              className={`card keyframe-card${isSelected ? ' keyframe-card--selected' : ''}`}
              key={`keyframe-${keyframe.keyframe_num}-${i}`}
            >
              <span className="result-rank" aria-label={`Rank ${i + 1}`}>
                {i + 1}
              </span>
              <label className="frame-select-toggle" title="Select frame for CSV export">
                <input
                  type="checkbox"
                  checked={isSelected}
                  onChange={() => toggleFrameSelection(keyframe.keyframe_num)}
                />
                <span className="frame-select-mark" />
              </label>
              <LazyImage
                className="thumb keyframe-thumb"
                src={src}
                alt={`${keyframe.video_id} - Frame ${keyframe.keyframe_num}`}
                fetchPriority={i < 4 ? 'high' : 'low'}
                onClick={() => setZoomedFrame(keyframe)}
                onError={() => {}}
              />
              <div className="meta">
                <div title={keyframe.keyframe_id}>
                  Frame {keyframe.keyframe_num}
                  {score && <span className="score-display">{score}</span>}
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
                      openPlayer(video.video_url, keyframe?.timestamp ?? 0, video.keyframes);
                    }}
                  >
                    {isValidVideoUrl(video.video_url) ? '📺 Play' : '📺 YouTube'}
                  </button>
                  <button
                    className="csv-btn"
                    onClick={(e) => {
                      e.stopPropagation();
                      downloadOneCSV(video.video_id, keyframe.keyframe_num);
                    }}
                  >
                    📊 CSV
                  </button>
                </div>
                <DresShotButton
                  videoId={video.video_id}
                  frame={keyframe.keyframe_num}
                  fps={fps}
                  rank={keyframe.result_rank ?? scoreRankByFrame.get(keyframe.keyframe_num)}
                />
              </div>
            </div>
          );
        })}
      </div>

      {zoomedFrame && (
        <div className="zoom-frame-overlay" onClick={() => setZoomedFrame(null)}>
          <div className="zoom-frame-container" onClick={(e) => e.stopPropagation()}>
            <div className="zoom-frame-header">
              <div className="zoom-frame-title">
                Frame {zoomedFrame.keyframe_num} - {zoomedFrame.video_id}
              </div>
              <button className="zoom-frame-close" onClick={() => setZoomedFrame(null)}>
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
                    {frameToMs(zoomedFrame.keyframe_num, resolveFps(video, zoomedFrame)) == null
                      ? 'thiếu fps'
                      : `${frameToMs(zoomedFrame.keyframe_num, resolveFps(video, zoomedFrame))} ms`}
                  </span>
                </div>
                <div className="zoom-frame-actions">
                  <button
                    className="youtube-btn zoom-youtube-btn"
                    onClick={() => openPlayer(video.video_url, zoomedFrame.timestamp, video.keyframes)}
                  >
                    {isValidVideoUrl(video.video_url) ? '📺 Play' : '📺 YouTube'}
                  </button>
                  <button
                    className="csv-btn zoom-csv-btn"
                    onClick={() => downloadOneCSV(video.video_id, zoomedFrame.keyframe_num)}
                  >
                    📊 Download CSV
                  </button>
                  <DresShotButton
                    videoId={video.video_id}
                    frame={zoomedFrame.keyframe_num}
                    fps={resolveFps(video, zoomedFrame)}
                    rank={zoomedFrame.result_rank ?? scoreRankByFrame.get(zoomedFrame.keyframe_num)}
                  />
                </div>
                {video && video.keyframes && (
                  <div style={{ marginTop: '10px' }}>
                    <div style={{ color: '#a2b0c6', marginBottom: '6px' }}>All frames in this video</div>
                    <div style={{ display: 'flex', overflowX: 'auto', gap: '8px', paddingBottom: '6px' }}>
                      {video.keyframes.map((kf, idx) => {
                        const kfSrc = kf.image_url || 'data:image/gif;base64,R0lGODlhAQABAAAAACw=';
                        const isActive = kf.keyframe_num === zoomedFrame.keyframe_num;
                        return (
                          <div
                            key={`strip-${video.video_id}-${idx}`}
                            style={{
                              minWidth: '120px',
                              border: isActive ? '2px solid #4da3ff' : '1px solid #2b3b52',
                              borderRadius: '6px',
                              padding: '2px',
                            }}
                          >
                            <LazyImage
                              wrapperClassName="strip-thumb-wrap"
                              className="strip-thumb"
                              src={kfSrc}
                              alt={`${video.video_id} - Frame ${kf.keyframe_num}`}
                              onClick={() => setZoomedFrame(kf)}
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
          videoId={selectedVideoId}
          csvBaseName={csvBaseName}
          frameRate={resolveFps(video, video.keyframes?.[0])}
          onClose={() => setPlayer({ open: false, url: '', t: 0, markers: [], keyframeRefs: [] })}
        />
      )}
    </>
  );
};

export default KeyframeGrid;
