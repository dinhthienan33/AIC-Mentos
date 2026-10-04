import React, { useState } from 'react';
import LazyImage from './LazyImage';
import ResultFilter from './ResultFilter';
import VideoPlayerModal from './VideoPlayModal';
import DresShotButton from './DresShotButton';
import { isValidVideoUrl } from '../utils/videoUtils';
import { normalizeOdResults } from '../utils/keyframePathUtils';
import { createPlayerState, EMPTY_PLAYER_STATE } from '../utils/videoPlayerUtils';
import { BACKEND_ORIGIN, apiUrl } from '../config';
import { submitOnEnter } from '../utils/submitOnEnter';

const ODSearchTab = () => {
  const [query, setQuery] = useState('');
  const [loading, setLoading] = useState(false);
  const [results, setResults] = useState([]);
  const [error, setError] = useState('');
  const [processingTime, setProcessingTime] = useState(null);
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

  const openPlayer = (result) => {
    if (!result?.video_url) return;
    setPlayer(createPlayerState({
      videoUrl: result.video_url,
      startSeconds: result.timestamp ?? 0,
      keyframeRefs: [result],
      videoId: result.video_id,
    }));
  };

  const handleSearch = async (event) => {
    event.preventDefault();
    if (!query.trim()) return;

    const finalTopK = resolvedTopK();
    setTopK(finalTopK);

    setError('');
    setProcessingTime(null);
    setLoading(true);
    setResults([]);

    try {
      const response = await fetch(apiUrl('/filter-search'), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          query: query.trim(),
          model_name: modelName,
          top_k: finalTopK,
          filtering: { od_text: [query.trim()] },
        }),
      });

      if (!response.ok) {
        throw new Error(`HTTP ${response.status}: ${response.statusText}`);
      }

      const data = await response.json();

      if (data.processing_time !== undefined) {
        setProcessingTime(data.processing_time);
      }

      const normalized = normalizeOdResults(data);
      if (normalized.length > 0) {
        setResults(normalized);
      } else {
        setError('No OD results found for your query');
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

  const normalizedResultKeyword = resultKeyword.trim().toLowerCase();
  const filteredResults = results.filter((result) => {
    if (!normalizedResultKeyword) return true;
    const searchableText = [
      result.video_id,
      result.group_id,
      result.keyframe_num,
      result.timestamp,
      ...(result.detected_objects || []),
    ].filter((value) => value !== undefined && value !== null).join(' ').toLowerCase();
    return normalizedResultKeyword
      .split(/[\s,]+/)
      .filter(Boolean)
      .every((token) => searchableText.includes(token));
  });

  return (
    <div className="asr-search-tab ocr-search-tab od-search-tab">
      <div className="search-container">
        <h2>OD Search</h2>
        <p className="description">
          Search through video content using Object Detection (OD) to find specific objects.
        </p>

        <form className="search-form" onSubmit={handleSearch}>
          <div className="control-group">
            <label>Search Query:</label>
            <textarea
              placeholder="Enter objects to search for (e.g., dog, car, person)..."
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
              <label>Top K Results:</label>
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
            {loading ? 'Searching...' : 'Search Objects'}
          </button>
        </form>

        {error && <div className="error-message">Error: {error}</div>}
        {!error && loading && <div className="loading-message">Searching for objects...</div>}
        {!error && !loading && processingTime && (
          <div className="processing-time">
            ⚡ OD search completed in {processingTime.toFixed(2)} seconds
          </div>
        )}

        {results.length > 0 && (
          <div className="results-container">
            <ResultFilter
              id="od-result-keyword"
              value={resultKeyword}
              onChange={setResultKeyword}
              filteredCount={filteredResults.length}
              totalCount={results.length}
              placeholder="Search video id / object / frame in results…"
            />
            <h3>OD Search Results ({results.length})</h3>
            <div className="ocr-results-grid">
              {filteredResults.map((result, index) => (
                <div
                  key={`${result.video_id}-${result.keyframe_num}-${index}`}
                  className="ocr-result-card"
                >
                  <LazyImage
                    className="ocr-result-thumb"
                    src={result.image_url || result.thumbnail_url}
                    alt={`${result.video_id || 'frame'} ${result.keyframe_num || index}`}
                    fetchPriority={index < 4 ? 'high' : 'low'}
                    onClick={() => result.video_url && openPlayer(result)}
                  />
                  <div className="ocr-result-body">
                    <div className="result-header">
                      <span className="result-number">#{index + 1}</span>
                      <span className="video-id">{result.video_id || 'Unknown'}</span>
                      {result.score != null && (
                        <span className="score">Score: {result.score.toFixed(3)}</span>
                      )}
                    </div>
                    {result.keyframe_num != null && (
                      <div className="ocr-frame-meta">Frame {result.keyframe_num}</div>
                    )}
                    <div className="object-names">
                      <h4>Detected Objects:</h4>
                      <div className="object-tags">
                        {result.detected_objects?.length > 0 ? (
                          result.detected_objects.map((obj, objIndex) => (
                            <span key={objIndex} className="object-tag">{obj}</span>
                          ))
                        ) : (
                          <span className="no-objects">No objects detected</span>
                        )}
                      </div>
                    </div>
                    {result.video_url && (
                      <button
                        type="button"
                        className="video-link-button"
                        onClick={() => openPlayer(result)}
                        title="Watch video with keyframe picker"
                      >
                        {isValidVideoUrl(result.video_url) ? '🎥 Watch' : '🎥 YouTube'}
                      </button>
                    )}
                    <DresShotButton
                      videoId={result.video_id}
                      frame={result.keyframe_num}
                      fps={result.fps}
                      source="od"
                    />
                  </div>
                </div>
              ))}
              {filteredResults.length === 0 && (
                <div className="status">No OD results match “{resultKeyword.trim()}”</div>
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

export default ODSearchTab;
