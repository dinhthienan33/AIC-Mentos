import React from 'react';

const ResultFilter = ({
  id,
  value,
  onChange,
  filteredCount,
  totalCount,
  unit = 'results',
  placeholder = 'Search within results…',
}) => (
  <div className="top-toolbar">
    <div className="result-filter">
      <label htmlFor={id}>Filter results:</label>
      <input
        id={id}
        type="search"
        value={value}
        onChange={(event) => onChange(event.target.value)}
        placeholder={placeholder}
      />
      {value.trim() && (
        <button
          type="button"
          className="result-filter-clear"
          onClick={() => onChange('')}
          title="Clear filter"
        >
          Clear
        </button>
      )}
      <span className="result-filter-count">
        {filteredCount}/{totalCount} {unit}
      </span>
    </div>
  </div>
);

export default ResultFilter;
