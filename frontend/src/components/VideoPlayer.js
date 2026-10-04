import React from 'react';
import { getSafeVideoUrl, isValidVideoUrl } from '../utils/videoUtils';

const VideoPlayer = ({ video, onClose }) => {
  const formatTime = (seconds) => {
    if (!seconds && seconds !== 0) return '0:00';
    const mins = Math.floor(seconds / 60);
    const secs = Math.floor(seconds % 60);
    return `${mins}:${secs.toString().padStart(2, '0')}`;
  };

  const calculateTimelinePosition = (time, totalDuration) => {
    if (!totalDuration || totalDuration <= 0) return 0;
    return Math.min((time / totalDuration) * 100, 100);
  };

  const navigateToVideo = (videoUrl) => {
    const safeUrl = getSafeVideoUrl(videoUrl);
    window.open(safeUrl, '_blank', 'noopener,noreferrer');
  };

  // Get safe video URL for iframe
  const safeVideoUrl = getSafeVideoUrl(video.video_url);
  const isVideoUrlValid = isValidVideoUrl(video.video_url);

  return (
    <div className="video-player-overlay">
      <div className="video-player-container">
        <div className="video-player-header">
          <div className="video-player-title">
            {video.video_id} - Frame {video.keyframe_num}
          </div>
          <button className="video-player-close" onClick={onClose}>
            ×
          </button>
        </div>
        
        <div className="video-player-content">
          {!isVideoUrlValid && (
            <div className="video-warning" style={{ 
              padding: '10px', 
              backgroundColor: '#fff3cd', 
              border: '1px solid #ffeaa7', 
              borderRadius: '4px', 
              marginBottom: '10px',
              color: '#856404'
            }}>
              ⚠️ Video URL not available. Opening YouTube homepage instead.
            </div>
          )}
          <iframe
            className="video-iframe"
            src={safeVideoUrl}
            title={`${video.video_id} Video Player`}
            allowFullScreen
            allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture"
          />
          
          <div className="video-timeline">
            <div className="timeline-header">
              <span>Video Timeline</span>
              <div className="timeline-info">
                <span className="timeline-marker timeline-start">
                  🎬 Start: {formatTime(video.start_time)}
                </span>
                {video.end_time && (
                  <span className="timeline-marker timeline-end">
                    ⏹️ End: {formatTime(video.end_time)}
                  </span>
                )}
              </div>
            </div>
            
            <div className="timeline-bar">
              <div
                className="timeline-progress"
                style={{ width: `${calculateTimelinePosition(video.end_time, video.end_time)}%` }}
              />
              <div
                className="timeline-start-marker"
                style={{ left: `${calculateTimelinePosition(video.start_time, video.end_time)}%` }}
              />
              {video.end_time && (
                <div
                  className="timeline-end-marker"
                  style={{ left: `${calculateTimelinePosition(video.end_time, video.end_time)}%` }}
                />
              )}
            </div>
            
            <div className="timeline-labels">
              <span>0:00</span>
              <span>{formatTime(video.end_time || 100)}</span>
            </div>
          </div>
          
          <button
            className="play-video-btn"
            onClick={() => navigateToVideo(video.video_url)}
          >
            {isVideoUrlValid ? '🎥 Open in YouTube' : '🎥 Open YouTube'}
          </button>
        </div>
      </div>
    </div>
  );
};

export default VideoPlayer;
