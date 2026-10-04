import React, { useMemo } from 'react';
import LazyImage from './LazyImage';

const sortVideos = (list, sortBy) => {
  const items = [...(list || [])];
  items.sort((a, b) => {
    switch (sortBy) {
      case 'name':
        return a.video_id.localeCompare(b.video_id);
      case 'score':
        return b.best_score - a.best_score;
      case 'frames':
        return b.keyframes.length - a.keyframes.length;
      default:
        return 0;
    }
  });
  return items;
};

const VideoList = ({ items, onExploreVideo, sortBy, rankItems }) => {
  const displayItems = items || [];
  const sortedItems = useMemo(() => sortVideos(displayItems, sortBy), [displayItems, sortBy]);

  const rawRankById = useMemo(() => {
    const source = sortVideos(
      rankItems && rankItems.length ? rankItems : displayItems,
      sortBy
    );
    const map = new Map();
    source.forEach((video, i) => {
      if (video?.video_id != null && !map.has(video.video_id)) {
        map.set(video.video_id, i + 1);
      }
    });
    return map;
  }, [rankItems, displayItems, sortBy]);

  if (displayItems.length === 0) {
    return null;
  }

  return (
    <div className="grid">
      {sortedItems.map((video, i) => {
        let bestFrame = null;
        if (video.keyframes && video.keyframes.length > 0) {
          bestFrame = [...video.keyframes].sort((a, b) => b.confidence_score - a.confidence_score)[0];
        }
        const thumbnailSrc = (bestFrame && bestFrame.image_url)
          || video.thumbnail_url
          || 'data:image/gif;base64,R0lGODlhAQABAAAAACw=';
        const rank = rawRankById.get(video.video_id) ?? (i + 1);

        return (
          <div
            className="video-card"
            key={`video-${video.video_id}-${i}`}
            onClick={() => onExploreVideo(video.video_id)}
          >
            <span className="result-rank" aria-label={`Rank ${rank}`}>
              {rank}
            </span>
            <LazyImage
              className="thumb"
              src={thumbnailSrc}
              alt={`${video.video_id} best keyframe`}
              fetchPriority={i < 4 ? 'high' : 'low'}
            />
            <div className="video-info">
              <div className="video-title">{video.video_id}</div>
              <div className="video-stats">
                <span className="keyframe-count">
                  {video.keyframes.length} frames
                </span>
                <span className="best-score">
                  {video.best_score.toFixed(3)}
                </span>
              </div>
              <div className="video-timestamp">
                <span className="timestamp">
                  {video.keyframes && video.keyframes[0] && video.keyframes[0].timestamp
                    ? `${video.keyframes[0].timestamp.toFixed(1)}s`
                    : 'N/A'}
                </span>
              </div>
            </div>
          </div>
        );
      })}
    </div>
  );
};

export default VideoList;
