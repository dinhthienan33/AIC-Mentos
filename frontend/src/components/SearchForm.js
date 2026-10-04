import React from 'react';
import { submitOnEnter } from '../utils/submitOnEnter';

const SearchForm = ({
  query,
  setQuery,
  csvBaseName,
  setCsvBaseName,
  k,
  setK,
  scoreThreshold,
  setScoreThreshold,
  modelName,
  setModelName,
  temporalSearch,
  setTemporalSearch,
  hybridSearch,
  setHybridSearch,
  translate,
  setTranslate,
  loading,
  onSearch,
  onCancel
}) => {
  const handleTxtUpload = async (e) => {
    const file = e.target.files && e.target.files[0];
    if (!file) return;
    const nameNoExt = (file.name || '').replace(/\.[^/.]+$/, '');
    try {
      const text = await file.text();
      setQuery(text);
    } catch (err) {
      // ignore read errors
    }
    // set CSV base name from file name
    setCsvBaseName(nameNoExt || 'query');
  };
  const handleSubmit = (e) => {
    e.preventDefault();
    onSearch();
  };

  const handleKeyDown = (e) => {
    submitOnEnter(e, () => onSearch());
  };

  return (
    <>
      <form className="search" onSubmit={handleSubmit}>
        <div className="control-group" style={{ marginBottom: '8px' }}>
          <label style={{ display: 'block' }}>Upload .txt (optional):</label>
          <input type="file" accept=".txt,text/plain" onChange={handleTxtUpload} />
        </div>
        <textarea
          id="visual-search-query"
          placeholder={temporalSearch ? "Describe a sequence of events (e.g., 'train running, then vehicles waiting, then people closing barriers')" : "Describe what you're looking for..."}
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={handleKeyDown}
          rows={6}
          style={{ resize: 'vertical', whiteSpace: 'pre-wrap', minHeight: '140px' }}
        />
        <div className="control-group" style={{ margin: '8px 0' }}>
          <label>Query name (used for CSV filename when no file uploaded):</label>
          <input
            id="visual-search-query-name"
            type="text"
            placeholder="enter a short name"
            value={csvBaseName}
            onChange={(e) => setCsvBaseName(e.target.value)}
          />
        </div>
        <button
          type="button"
          onClick={onSearch}
          disabled={loading}
          className={temporalSearch ? 'temporal-active' : hybridSearch ? 'hybrid-active' : ''}
        >
          {loading ? 'Searching…' : temporalSearch ? 'Temporal Search' : hybridSearch ? 'Hybrid Search' : 'Search'}
        </button>
        {loading && (
          <button
            type="button"
            className="cancel"
            onClick={onCancel}
          >
            Cancel
          </button>
        )}
      </form>

      <div className="controls">
        <div className="control-group">
          <label>Results:</label>
          <input
            type="number"
            min="1"
            max="1000"
            placeholder="10"
            value={k}
            onChange={(e) => {
              const value = e.target.value;
              if (value === '') {
                setK(''); // Allow empty while typing
              } else {
                const numValue = parseInt(value);
                if (!isNaN(numValue)) {
                  setK(numValue);
                }
              }
            }}
          />
        </div>

        <div className="control-group">
          <label>Score:</label>
          <input
            type="number"
            min="0"
            max="1"
            step="0.1"
            placeholder="0.0"
            value={scoreThreshold}
            onChange={(e) => {
              const value = e.target.value;
              if (value === '') {
                setScoreThreshold(''); // Allow empty while typing
              } else {
                const numValue = parseFloat(value);
                if (!isNaN(numValue)) {
                  setScoreThreshold(numValue);
                }
              }
            }}
          />
        </div>

        <div className="control-group">
          <label>Model:</label>
          <select
            value={modelName}
            onChange={(e) => setModelName(e.target.value)}
          >
            <option value="siglip2">SigLIP2</option>
            <option value="jinav2">Jina V2 (alias)</option>
            <option value="jinav1">Jina V1 (alias)</option>
            <option value="blip2">BLIP2 (alias)</option>
          </select>
        </div>

        <div className="control-group">
          <label>
            <input
              type="checkbox"
              checked={hybridSearch}
              onChange={(e) => {
                const on = e.target.checked;
                setHybridSearch(on);
                if (on) setTemporalSearch(false);
              }}
            />
            Hybrid
          </label>
          <div className="control-help">
            Visual SigLIP + enrichment FTS rerank (slower, better on detailed KIS)
          </div>
        </div>

        <div className="control-group">
          <label>
            <input
              type="checkbox"
              checked={temporalSearch}
              onChange={(e) => {
                const on = e.target.checked;
                setTemporalSearch(on);
                if (on) setHybridSearch(false);
              }}
            />
            Temporal Search
          </label>
          <div className="control-help">
            Break down complex queries into sequential events
          </div>
        </div>

        <div className="control-group">
          <label>
            <input
              type="checkbox"
              checked={translate}
              onChange={(e) => setTranslate(e.target.checked)}
            />
            Translate (LLM)
          </label>
          <div className="control-help">
            Mặc định ON: gõ query tiếng Việt. Tắt chỉ khi muốn search đúng câu gốc.
          </div>
        </div>

      </div>
    </>
  );
};

export default SearchForm;
