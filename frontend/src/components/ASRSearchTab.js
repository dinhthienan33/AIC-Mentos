import React, { useState } from 'react';
import VideoPlayerModal from './VideoPlayModal';
import ResultFilter from './ResultFilter';
import DresShotButton from './DresShotButton';
import { getYouTubeEmbedUrl, isValidVideoUrl } from '../utils/videoUtils';
import { createPlayerState, EMPTY_PLAYER_STATE, toKeyframeRef } from '../utils/videoPlayerUtils';
import { BACKEND_ORIGIN, apiUrl } from '../config';
import { submitOnEnter } from '../utils/submitOnEnter';

const formatTime = (seconds) => {
  const safe = Number(seconds) || 0;
  const mins = Math.floor(safe / 60);
  const secs = Math.floor(safe % 60);
  return `${mins}:${secs.toString().padStart(2, '0')}`;
};

const formatSegmentLabel = (segment, index) => {
  if (segment?.segment_id !== undefined && segment?.segment_id !== null) {
    return `Segment #${segment.segment_id}`;
  }
  return `Segment #${index + 1}`;
};

const formatScore = (score) => {
  if (score === undefined || score === null || Number.isNaN(Number(score))) {
    return 'N/A';
  }
  return Number(score).toFixed(3);
};

const highlightQuery = (text, query) => {
  if (!text || !query?.trim()) return text;

  const escaped = query.trim().replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const parts = text.split(new RegExp(`(${escaped})`, 'gi'));

  return parts.map((part, index) =>
    part.toLowerCase() === query.trim().toLowerCase() ? (
      <mark key={`${part}-${index}`} className="asr-text-highlight">{part}</mark>
    ) : (
      part
    )
  );
};

const segmentToKeyframe = (segment, videoName) =>
  toKeyframeRef({
    video_id: videoName,
    keyframe_num: segment.start_frame,
    timestamp: segment.start_time,
    fps: segment.fps,
    frame_rate: segment.frame_rate,
  });

const ASRSearchTab = () => {
  const [query, setQuery] = useState('');
  const [loading, setLoading] = useState(false);
  const [results, setResults] = useState([]);
  const [error, setError] = useState('');
  const [processingTime, setProcessingTime] = useState(null);
  const [modelUsed, setModelUsed] = useState('');
  const [totalResults, setTotalResults] = useState(null);
  const [modelName, setModelName] = useState('siglip2');
  const [topK, setTopK] = useState(10);
  const [player, setPlayer] = useState(EMPTY_PLAYER_STATE);
  const [resultKeyword, setResultKeyword] = useState('');

  const handleTopKChange = (event) => {
    const value = event.target.value;
    if (value === '') {
      setTopK('');
      return;
    }
    const numValue = parseInt(value, 10);
    if (!Number.isNaN(numValue)) {
      setTopK(numValue);
    }
  };

  const resolvedTopK = () => {
    const n = Number(topK);
    if (!Number.isFinite(n)) return 10;
    return Math.min(500, Math.max(1, n));
  };

  const openPlayer = (videoUrl, startSeconds, keyframeRefs, videoId, frameRate) => {
    if (!videoUrl) return;
    setPlayer(createPlayerState({
      videoUrl,
      startSeconds,
      keyframeRefs,
      videoId,
      frameRate,
    }));
  };

  const handleSearch = async (event) => {
    event.preventDefault();
    if (!query.trim()) return;

    const finalTopK = resolvedTopK();
    setTopK(finalTopK);
    setError('');
    setProcessingTime(null);
    setModelUsed('');
    setTotalResults(null);
    setLoading(true);
    setResults([]);

    try {
      const response = await fetch(apiUrl('/asr-search'), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          query: query.trim(),
          model_name: modelName,
          top_k: finalTopK,
        }),
      });

      if (!response.ok) {
        throw new Error(`HTTP ${response.status}: ${response.statusText}`);
      }

      const data = await response.json();

      if (data.processing_time !== undefined) {
        setProcessingTime(data.processing_time);
      }
      if (data.model_used) {
        setModelUsed(data.model_used);
      }
      if (data.total_results !== undefined) {
        setTotalResults(data.total_results);
      }

      if (data.results && data.results.length > 0) {
        setResults(data.results);
      } else {
        setError('No ASR results found for your query');
      }
    } catch (err) {
      if (err.message.includes('Failed to fetch')) {
        setError(`Unable to connect to backend at ${BACKEND_ORIGIN}`);
      } else {
        setError(String(err?.message || err));
      }
    } finally {
      setLoading(false);
    }
  };

  const handleKeyDown = (event) => {
    submitOnEnter(event, handleSearch);
  };

  const segmentCount = results.reduce(
    (sum, video) => sum + (video.segments?.length || 0),
    0
  );
  const normalizedResultKeyword = resultKeyword.trim().toLowerCase();
  const filteredResults = results.filter((video) => {
    if (!normalizedResultKeyword) return true;
    const searchableText = [
      video.video_name,
      video.video_id,
      ...(video.segments || []).flatMap((segment) => [
        segment.text,
        segment.start_time,
        segment.end_time,
      ]),
    ].filter((value) => value !== undefined && value !== null).join(' ').toLowerCase();
    return normalizedResultKeyword
      .split(/[\s,]+/)
      .filter(Boolean)
      .every((token) => searchableText.includes(token));
  });

  return (
    <div className="asr-search-tab">
      <div className="search-container">
        <h2>ASR Search</h2>
        <p className="description">
          Search through video content using Automatic Speech Recognition (ASR) transcripts.
        </p>

        <form className="search-form" onSubmit={handleSearch}>
          <div className="control-group">
            <label>Search Query:</label>
            <textarea
              placeholder="Enter your search terms for ASR content..."
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              onKeyDown={handleKeyDown}
              rows={4}
              style={{ resize: 'vertical', minHeight: '100px' }}
            />
          </div>

          <div className="search-controls">
            <div className="control-group">
              <label>Model:</label>
              <select value={modelName} onChange={(e) => setModelName(e.target.value)}>
                <option value="siglip2">SigLIP2</option>
                <option value="jinav2">JinaV2 (alias)</option>
              </select>
            </div>

            <div className="control-group">
              <label>Top K Segments:</label>
              <input
                type="number"
                min="1"
                max="500"
                value={topK}
                onChange={handleTopKChange}
              />
            </div>
          </div>

          <button type="submit" disabled={loading} className="search-button">
            {loading ? 'Searching...' : 'Search ASR'}
          </button>
        </form>

        {error && <div className="error-message">Error: {error}</div>}
        {!error && loading && <div className="loading-message">Searching ASR content...</div>}
        {!error && !loading && processingTime !== null && (
          <div className="processing-time">
            ⚡ ASR search completed in {processingTime.toFixed(2)} seconds
            {modelUsed && <span className="search-method-indicator"> · Model: {modelUsed}</span>}
          </div>
        )}

        {results.length > 0 && (
          <div className="results-container">
            <ResultFilter
              id="asr-result-keyword"
              value={resultKeyword}
              onChange={setResultKeyword}
              filteredCount={filteredResults.length}
              totalCount={results.length}
              unit="videos"
              placeholder="Search video id / transcript in results…"
            />
            <h3>
              ASR Search Results ({segmentCount || totalResults || 0} segment
              {(segmentCount || totalResults || 0) === 1 ? '' : 's'} across {results.length} video
              {results.length === 1 ? '' : 's'})
            </h3>
            <div className="results-list">
              {filteredResults.map((video, index) => {
                const videoName = video.video_name || video.video_id || 'Unknown';
                const firstSegment = video.segments?.[0];
                const primaryUrl = firstSegment?.video_url || video.video_url;
                const segmentRefs = (video.segments || []).map((s) => segmentToKeyframe(s, videoName));
                const embedUrl = getYouTubeEmbedUrl(primaryUrl, firstSegment?.start_time ?? 0);

                return (
                  <div key={`${videoName}-${index}`} className="result-item">
                    <div className="result-header">
                      <span className="result-number">#{index + 1}</span>
                      <span className="video-id">Video: {videoName}</span>
                      <span className="score">Score: {formatScore(video.score)}</span>
                    </div>

                    {embedUrl && (
                      <div className="asr-video-preview">
                        <iframe
                          className="asr-video-iframe"
                          src={embedUrl}
                          title={`${videoName} preview`}
                          allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture"
                          allowFullScreen
                        />
                      </div>
                    )}

                    {primaryUrl && (
                      <div className="asr-video-actions">
                        <button
                          type="button"
                          className="video-link-button"
                          onClick={() => openPlayer(
                            primaryUrl,
                            firstSegment?.start_time ?? 0,
                            segmentRefs,
                            videoName,
                            firstSegment?.fps
                          )}
                          title="Watch video with keyframe picker"
                        >
                          {isValidVideoUrl(primaryUrl) ? '🎥 Watch' : '🎥 YouTube'}
                        </button>
                      </div>
                    )}

                    {video.segments && video.segments.length > 0 ? (
                      <div className="segments-container">
                        <h4>Matching Segments ({video.segments.length})</h4>
                        {video.segments.map((segment, segIndex) => (
                          <div key={`${videoName}-${segment.segment_id ?? segIndex}`} className="segment-item">
                            <div className="segment-header">
                              <span className="segment-id">{formatSegmentLabel(segment, segIndex)}</span>
                              <span className="segment-time">
                                {formatTime(segment.start_time)} - {formatTime(segment.end_time)}
                                <span className="duration">
                                  ({segment.duration != null ? Number(segment.duration).toFixed(2) : '0.00'}s)
                                </span>
                              </span>
                              <span className="score">Score: {formatScore(segment.score)}</span>
                              {segment.video_url && (
                                <button
                                  type="button"
                                  className="video-link-button"
                                  onClick={() => openPlayer(
                                    segment.video_url,
                                    segment.start_time ?? 0,
                                    segmentRefs,
                                    videoName,
                                    segment.fps
                                  )}
                                  title="Watch video at this segment"
                                >
                                  {isValidVideoUrl(segment.video_url) ? '🎥 Watch' : '🎥 YouTube'}
                                </button>
                              )}
                            </div>

                            <div className="segment-text">
                              {highlightQuery(segment.text || 'No transcript available', query)}
                            </div>

                            {(segment.start_frame != null || segment.end_frame != null) && (
                              <div className="frame-info">
                                <span className="frame-range">
                                  Frames: {segment.start_frame ?? 'N/A'} - {segment.end_frame ?? 'N/A'}
                                </span>
                              </div>
                            )}
                            <DresShotButton
                              videoId={videoName}
                              frame={segment.start_frame}
                              fps={segment.fps}
                              source="asr"
                            />
                          </div>
                        ))}
                      </div>
                    ) : (
                      <div className="segments-container">
                        <h4>No Segments Found</h4>
                        <p className="asr-empty-note">
                          This video has no matching transcript segments for your search query.
                        </p>
                      </div>
                    )}
                  </div>
                );
              })}
              {filteredResults.length === 0 && (
                <div className="status">No ASR results match “{resultKeyword.trim()}”</div>
              )}
            </div>
          </div>
        )}
      </div>

      {player.open && (
        <VideoPlayerModal
          videoUrl={player.url}
          startSeconds={player.t}
          markers={player.markers}
          keyframeRefs={player.keyframeRefs}
          videoId={player.videoId}
          csvBaseName={player.csvBaseName}
          frameRate={player.frameRate}
          onClose={() => setPlayer(EMPTY_PLAYER_STATE)}
        />
      )}
    </div>
  );
};

export default ASRSearchTab;
