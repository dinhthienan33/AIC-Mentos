import React, { useState } from 'react';
import LazyImage from './LazyImage';
import ResultFilter from './ResultFilter';
import VideoPlayerModal from './VideoPlayModal';
import DresShotButton from './DresShotButton';
import { isValidVideoUrl } from '../utils/videoUtils';
import { createPlayerState, EMPTY_PLAYER_STATE } from '../utils/videoPlayerUtils';
import { BACKEND_ORIGIN, apiUrl } from '../config';
import { submitOnEnter } from '../utils/submitOnEnter';

const OCRSearchTab = () => {
  const [query, setQuery] = useState('');
  const [loading, setLoading] = useState(false);
  const [results, setResults] = useState([]);
  const [error, setError] = useState('');
  const [processingTime, setProcessingTime] = useState(null);
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
      const response = await fetch(apiUrl('/ocr-search'), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          query: query.trim(),
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

      if (data.results && data.results.length > 0) {
        setResults(data.results);
      } else {
        setError('No OCR results found for your query');
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
      result.ocr_text,
      result.highlight,
      result.keyframe_num,
      result.timestamp,
    ].filter((value) => value !== undefined && value !== null).join(' ').toLowerCase();
    return normalizedResultKeyword
      .split(/[\s,]+/)
      .filter(Boolean)
      .every((token) => searchableText.includes(token));
  });

  return (
    <div className="asr-search-tab ocr-search-tab">
      <div className="search-container">
        <h2>OCR Search</h2>
        <p className="description">
          Search keyframes by on-screen text detected with OCR.
        </p>

        <form className="search-form" onSubmit={handleSearch}>
          <div className="control-group">
            <label>Search Query:</label>
            <textarea
              placeholder="Enter text to find in video frames (e.g. taxi, STOP, 7-Eleven)..."
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              onKeyDown={handleKeyDown}
              rows={4}
              style={{ resize: 'vertical', minHeight: '100px' }}
            />
          </div>

          <div className="search-controls">
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
            {loading ? 'Searching...' : 'Search OCR'}
          </button>
        </form>

        {error && <div className="error-message">Error: {error}</div>}
        {!error && loading && <div className="loading-message">Searching OCR text...</div>}
        {!error && !loading && processingTime && (
          <div className="processing-time">
            ⚡ OCR search completed in {processingTime.toFixed(2)} seconds
          </div>
        )}

        {results.length > 0 && (
          <div className="results-container">
            <ResultFilter
              id="ocr-result-keyword"
              value={resultKeyword}
              onChange={setResultKeyword}
              filteredCount={filteredResults.length}
              totalCount={results.length}
              placeholder="Search video id / OCR text / frame in results…"
            />
            <h3>OCR Search Results ({results.length})</h3>
            <div className="ocr-results-grid">
              {filteredResults.map((result, index) => (
                <div key={`${result.video_id}-${result.keyframe_num}-${index}`} className="ocr-result-card">
                  <LazyImage
                    className="ocr-result-thumb"
                    src={result.image_url || result.thumbnail_url}
                    alt={`${result.video_id} frame ${result.keyframe_num}`}
                    fetchPriority={index < 4 ? 'high' : 'low'}
                    onClick={() => result.video_url && openPlayer(result)}
                  />
                  <div className="ocr-result-body">
                    <div className="result-header">
                      <span className="result-number">#{index + 1}</span>
                      <span className="video-id">{result.video_id}</span>
                      <span className="score">
                        Score: {result.confidence_score ? result.confidence_score.toFixed(3) : 'N/A'}
                      </span>
                    </div>
                    <div className="ocr-frame-meta">
                      Frame {result.keyframe_num}
                      {result.timestamp != null && (
                        <span className="timestamp"> · {result.timestamp.toFixed(1)}s</span>
                      )}
                    </div>
                    <div className="ocr-text-block">
                      {result.highlight ? (
                        <span dangerouslySetInnerHTML={{ __html: result.highlight }} />
                      ) : (
                        result.ocr_text || 'No OCR text'
                      )}
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
                      source="ocr"
                    />
                  </div>
                </div>
              ))}
              {filteredResults.length === 0 && (
                <div className="status">No OCR results match “{resultKeyword.trim()}”</div>
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

export default OCRSearchTab;
