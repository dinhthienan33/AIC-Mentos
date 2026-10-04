import React, { useEffect, useMemo, useState } from 'react';
import { apiUrl } from '../config';

const SearchLevelSelector = ({
  searchLevel,
  setSearchLevel,
  selectedGroups,
  setSelectedGroups,
  selectedVideos,
  setSelectedVideos,
}) => {
  const [catalog, setCatalog] = useState({ status: 'loading', groups: [], message: '' });
  const [groupFilter, setGroupFilter] = useState('');
  const [videoFilter, setVideoFilter] = useState('');

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const res = await fetch(apiUrl('/catalog'));
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();
        if (!cancelled) {
          setCatalog({ status: 'ready', groups: data.groups || [], message: '' });
        }
      } catch (err) {
        if (!cancelled) {
          setCatalog({
            status: 'error',
            groups: [],
            message: err.message || 'Could not load groups',
          });
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const groups = catalog.groups;

  useEffect(() => {
    if (searchLevel === 'all') {
      setSelectedGroups([]);
      setSelectedVideos([]);
    } else if (searchLevel === 'group') {
      setSelectedVideos([]);
    }
  }, [searchLevel, setSelectedGroups, setSelectedVideos]);

  const availableVideos = useMemo(() => {
    const selected = new Set(selectedGroups);
    const videos = [];
    groups.forEach((group) => {
      if (!selected.has(group.id)) return;
      (group.videos || []).forEach((video) => {
        videos.push({
          id: video.id,
          frames: video.frames,
          group: group.id,
        });
      });
    });
    return videos;
  }, [groups, selectedGroups]);

  const visibleGroups = useMemo(() => {
    const q = groupFilter.trim().toLowerCase();
    if (!q) return groups;
    return groups.filter((group) => group.id.toLowerCase().includes(q));
  }, [groups, groupFilter]);

  const visibleVideos = useMemo(() => {
    const q = videoFilter.trim().toLowerCase();
    if (!q) return availableVideos;
    return availableVideos.filter(
      (video) => video.id.toLowerCase().includes(q) || video.group.toLowerCase().includes(q)
    );
  }, [availableVideos, videoFilter]);

  const handleGroupToggle = (groupId) => {
    setSelectedGroups((prev) => (
      prev.includes(groupId) ? prev.filter((id) => id !== groupId) : [...prev, groupId]
    ));
    setSelectedVideos([]);
  };

  const handleVideoToggle = (videoId) => {
    setSelectedVideos((prev) => (
      prev.includes(videoId) ? prev.filter((id) => id !== videoId) : [...prev, videoId]
    ));
  };

  const handleSelectAll = () => {
    if (searchLevel === 'group') {
      setSelectedGroups(visibleGroups.map((group) => group.id));
    } else if (searchLevel === 'video') {
      setSelectedVideos(visibleVideos.map((video) => video.id));
    }
  };

  const handleClearAll = () => {
    if (searchLevel === 'group') setSelectedGroups([]);
    else if (searchLevel === 'video') setSelectedVideos([]);
  };

  const getCurrentSelection = () => {
    if (searchLevel === 'group') {
      if (selectedGroups.length === 0) return 'Select groups';
      if (selectedGroups.length === 1) return selectedGroups[0];
      return `${selectedGroups.length} groups selected`;
    }
    if (searchLevel === 'video') {
      if (selectedVideos.length === 0) return 'Select videos';
      if (selectedVideos.length === 1) return selectedVideos[0];
      return `${selectedVideos.length} videos selected`;
    }
    return 'All data';
  };

  return (
    <div className="search-level-selector">
      <div className="level-selector-header">
        <h3>Search Level</h3>
        <div className="current-selection">
          {getCurrentSelection()}
        </div>
      </div>

      <div className="level-buttons">
        <button
          className={`level-btn ${searchLevel === 'all' ? 'active' : ''}`}
          onClick={() => setSearchLevel('all')}
        >
          All
        </button>
        <button
          className={`level-btn ${searchLevel === 'group' ? 'active' : ''}`}
          onClick={() => setSearchLevel('group')}
        >
          Group
        </button>
        <button
          className={`level-btn ${searchLevel === 'video' ? 'active' : ''}`}
          onClick={() => setSearchLevel('video')}
          disabled={selectedGroups.length === 0}
        >
          Video
        </button>
      </div>

      {searchLevel === 'group' && (
        <div className="selection-panel">
          <div className="selection-header">
            <h4>Select Groups</h4>
            <div className="selection-actions">
              <button className="action-btn" onClick={handleSelectAll} disabled={!visibleGroups.length}>Select All</button>
              <button className="action-btn" onClick={handleClearAll}>Clear All</button>
            </div>
          </div>
          <input
            className="level-filter-input"
            type="text"
            placeholder="Filter groups"
            value={groupFilter}
            onChange={(e) => setGroupFilter(e.target.value)}
          />
          {catalog.status === 'loading' && <div className="selection-summary">Loading groups…</div>}
          {catalog.status === 'error' && (
            <div className="selection-summary">Could not load groups ({catalog.message}).</div>
          )}
          <div className="group-grid">
            {visibleGroups.map((group) => (
              <label key={group.id} className="group-card-container">
                <input
                  type="checkbox"
                  checked={selectedGroups.includes(group.id)}
                  onChange={() => handleGroupToggle(group.id)}
                  className="group-checkbox"
                />
                <div className={`group-card ${selectedGroups.includes(group.id) ? 'selected' : ''}`}>
                  <div className="group-name">{group.id}</div>
                  <div className="group-info">{group.video_count} videos</div>
                  <div className="group-batch">{group.frames} frames</div>
                </div>
              </label>
            ))}
          </div>
        </div>
      )}

      {searchLevel === 'video' && selectedGroups.length > 0 && (
        <div className="selection-panel">
          <div className="selection-header">
            <h4>Select Videos from {selectedGroups.length} {selectedGroups.length === 1 ? 'Group' : 'Groups'}</h4>
            <div className="selection-actions">
              <button className="action-btn" onClick={handleSelectAll} disabled={!visibleVideos.length}>Select All</button>
              <button className="action-btn" onClick={handleClearAll}>Clear All</button>
            </div>
          </div>
          <input
            className="level-filter-input"
            type="text"
            placeholder="Filter videos"
            value={videoFilter}
            onChange={(e) => setVideoFilter(e.target.value)}
          />
          <div className="video-grid">
            {visibleVideos.map((video) => (
              <label key={video.id} className="video-card-container">
                <input
                  type="checkbox"
                  checked={selectedVideos.includes(video.id)}
                  onChange={() => handleVideoToggle(video.id)}
                  className="video-checkbox"
                />
                <div className={`video-card ${selectedVideos.includes(video.id) ? 'selected' : ''}`}>
                  <div className="video-name">{video.id}</div>
                  <div className="video-id">{video.group}</div>
                  <div className="video-batch">{video.frames} frames</div>
                </div>
              </label>
            ))}
          </div>
        </div>
      )}

      {searchLevel !== 'all' && (
        <div className="selection-summary">
          <strong>Search Scope:</strong>
          <div className="scope-path">
            {searchLevel === 'group' && selectedGroups.length > 0 &&
              `Groups: ${selectedGroups.join(', ')}`}
            {searchLevel === 'video' && selectedVideos.length > 0 &&
              `${selectedVideos.length} videos from ${selectedGroups.length} groups`}
          </div>
        </div>
      )}
    </div>
  );
};

export default SearchLevelSelector;
