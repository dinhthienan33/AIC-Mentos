import React, { useEffect, useMemo, useRef, useState } from 'react';
import SearchForm from './SearchForm';
import VideoList from './VideoList';
import KeyframeGrid from './KeyframeGrid';
import AllFramesView from './AllFramesView';
import ASRSearchTab from './ASRSearchTab';
import ODSearchTab from './ODSearchTab';
import OCRSearchTab from './OCRSearchTab';
import CCTVTab from './CCTVTab';
import ShotOcrTab from './ShotOcrTab';
import VoiceAsrTab from './VoiceAsrTab';
import SearchLevelSelector from './SearchLevelSelector';
import ResizableSidebar from './ResizableSidebar';
import { BACKEND_ORIGIN, apiUrl } from '../config';
import { downloadJson } from '../utils/csvUtils';
import { pathForTab, tabFromPath } from '../tabRoutes';
import { useDres } from '../dres/DresContext';

const matchResultKeyword = (video, keyword) => {
  const raw = (keyword || '').trim().toLowerCase();
  if (!raw) return true;
  const tokens = raw.split(/[\s,]+/).filter(Boolean);
  if (!tokens.length) return true;
  const parts = [
    video.video_id,
    video.group_id,
    video.video_url,
    ...((video.keyframes || []).flatMap((kf) => [
      kf.video_id,
      kf.group_id,
      kf.keyframe_id,
      kf.full_name,
      kf.image_path,
      kf.query,
      String(kf.keyframe_num ?? ''),
      String(kf.timestamp ?? ''),
    ])),
  ];
  const haystack = parts.filter(Boolean).join(' ').toLowerCase();
  return tokens.every((token) => haystack.includes(token));
};

const SearchInterface = ({ onOpenVideo }) => {
  const [activeTab, setActiveTabState] = useState(() => tabFromPath(window.location.pathname));

  useEffect(() => {
    const onPopState = () => setActiveTabState(tabFromPath(window.location.pathname));
    window.addEventListener('popstate', onPopState);
    return () => window.removeEventListener('popstate', onPopState);
  }, []);

  const setActiveTab = (tab) => {
    setActiveTabState(tab);
    const nextPath = pathForTab(tab);
    if (window.location.pathname !== nextPath) {
      window.history.pushState({ tab }, '', nextPath);
    }
  };
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(false);
  const [items, setItems] = useState([]);
  const [error, setError] = useState("");
  const [abortController, setAbortController] = useState(null);
  const [k, setK] = useState(10);
  const [modelName, setModelName] = useState('siglip2');
  const [scoreThreshold, setScoreThreshold] = useState(0.0);
  const [temporalSearch, setTemporalSearch] = useState(false);
  const [hybridSearch, setHybridSearch] = useState(false);
  const [translate, setTranslate] = useState(true);
  const [processingTime, setProcessingTime] = useState(null);
  const [viewMode, setViewMode] = useState('videos'); // 'videos' or 'frames'
  const [videoView, setVideoView] = useState('list'); // 'list' or 'keyframes'
  const [selectedVideoId, setSelectedVideoId] = useState(null);
  const [videoData, setVideoData] = useState({}); // Store grouped video data
  const [frameSortBy, setFrameSortBy] = useState('score'); // shared frames sort
  const [videoListSortBy, setVideoListSortBy] = useState('score'); // persist video list sort
  const [csvBaseName, setCsvBaseName] = useState('');
  const [videosScrollY, setVideosScrollY] = useState(0);
  const [resultKeyword, setResultKeyword] = useState('');

  // Filtering UI state
  const [filteringEnabled, setFilteringEnabled] = useState(false);
  const [filterOd, setFilterOd] = useState(false);
  const [filterOcr, setFilterOcr] = useState(false);
  const [filterAsr, setFilterAsr] = useState(false);
  const [odValues, setOdValues] = useState(['']);
  const [ocrValues, setOcrValues] = useState(['']);
  const [asrValues, setAsrValues] = useState(['']);

  // Exclusion controls
  const [excludeByGroup, setExcludeByGroup] = useState(false);
  const [excludeGroupsText, setExcludeGroupsText] = useState(""); // comma-separated groups e.g. L21,L22
  const [excludeByVideo, setExcludeByVideo] = useState(false);
  const [excludeVidIdsText, setExcludeVidIdsText] = useState(""); // comma-separated video ids e.g. L21_V001,L22_V003
  const [batch, setBatch] = useState(1); // batch selection: 1 or 2

  // Search level selector state
  const [searchLevel, setSearchLevel] = useState('all'); // 'all', 'batch', 'group', 'video'
  const [selectedBatches, setSelectedBatches] = useState([]);
  const [selectedGroups, setSelectedGroups] = useState([]);
  const [selectedVideos, setSelectedVideos] = useState([]);
  const { registerSearchApi, taskType } = useDres();

  useEffect(() => {
    registerSearchApi({
      setQuery,
      setK,
      setTranslate,
      setTemporalSearch,
      setHybridSearch,
      setModelName,
      ensureQueryName: () => {
        setCsvBaseName((current) => (current && current.trim() ? current : `q${Date.now().toString().slice(-4)}`));
      },
      setFilteringEnabled,
      setFilterOcr,
      setFilterOd,
    });
  }, [registerSearchApi]);

  useEffect(() => {
    if (taskType !== 'trake') return;
    setModelName('siglip2');
    setTranslate(true);
    setTemporalSearch(true);
    setHybridSearch(false);
  }, [taskType]);

  const doSearch = async (overrideQuery) => {
    const searchText = (typeof overrideQuery === 'string' ? overrideQuery : query).trim();
    setError("");
    setProcessingTime(null);
    if (!searchText) return;
    if (!csvBaseName || !csvBaseName.trim()) {
      setError('Please set a query name (or upload a .txt file).');
      return;
    }

    
    // Validate and set defaults for empty fields
    const finalK = k === '' || k === null || k === undefined ? 10 : parseInt(k) || 10;
    const finalScoreThreshold = scoreThreshold === '' || scoreThreshold === null || scoreThreshold === undefined ? 0.0 : parseFloat(scoreThreshold) || 0.0;
    
    // Update state with validated values
    if (k !== finalK) setK(finalK);
    if (scoreThreshold !== finalScoreThreshold) setScoreThreshold(finalScoreThreshold);
    
    setLoading(true);
    setVideoView('list'); // Reset to list view
    setSelectedVideoId(null);
    setResultKeyword('');
    
    const controller = new AbortController();
    setAbortController(controller);
    
    try {
      // Show immediate feedback
      setItems([]);
      setVideoData({});

      // Real API call
      // Build filtering object if enabled
      const listFromValues = (values) =>
        (values || []).map(v => (v || '').trim()).filter(v => v.length > 0);
      const filtering = {};
      if (filteringEnabled) {
        if (filterOd) filtering.od_text = listFromValues(odValues);
        if (filterOcr) filtering.ocr_text = listFromValues(ocrValues);
        if (filterAsr) filtering.asr_text = listFromValues(asrValues);
      }

      // Search scope is now handled in the request body building below

      const searchMethod = filteringEnabled
        ? "filtering"
        : temporalSearch
          ? "temporal"
          : hybridSearch
            ? "hybrid"
            : "normal";

      // Build request body
      const requestBody = { 
        query: searchText,
        top_k: finalK,
        score_threshold: finalScoreThreshold,
        model_name: modelName,
        search_method: searchMethod,
        translate: Boolean(translate),
        filtering: filtering && Object.keys(filtering).length > 0 ? filtering : undefined,
        // New exclusion fields
        exclude_groups: excludeByGroup
          ? (excludeGroupsText || "").split(',').map(s => s.trim()).filter(Boolean)
          : undefined,
        exclude_vid_id: excludeByVideo
          ? (excludeVidIdsText || "").split(',').map(s => s.trim()).filter(Boolean)
          : undefined,
      };

      if (searchLevel === 'group') {
        if (selectedGroups.length === 0) {
          setLoading(false);
          setError('Select at least one group.');
          return;
        }
        requestBody.include_groups = selectedGroups;
      } else if (searchLevel === 'video') {
        if (selectedVideos.length === 0) {
          setLoading(false);
          setError('Select at least one video.');
          return;
        }
        requestBody.include_videos = selectedVideos;
      }

      const fetchPromise = fetch(apiUrl('/search'), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(requestBody),
        signal: controller.signal
      });
      const res = await fetchPromise;
      if (!res.ok) throw new Error(`HTTP ${res.status}: ${res.statusText}`);
      const data = await res.json();
      
      // Set processing time if available
      if (data.processing_time !== undefined) {
        setProcessingTime(data.processing_time);
      }
      
      // Extract results from API format
      let results = data.results || [];
      
      if (results.length === 0) {
        setError('No results found for your query');
      } else {
        // Always group by video in new UI
        const grouped = {};
        const resultRank = new Map();
        [...results]
          .sort((a, b) => (b.confidence_score || 0) - (a.confidence_score || 0))
          .forEach((item, index) => {
            const key = `${item.video_id}|${item.keyframe_num}`;
            if (!resultRank.has(key)) resultRank.set(key, index + 1);
          });
        results.forEach(item => {
          item.result_rank = resultRank.get(`${item.video_id}|${item.keyframe_num}`);
          const videoId = item.video_id;
          if (!grouped[videoId]) {
            grouped[videoId] = {
              video_id: videoId,
              group_id: item.group_id,
              video_url: item.video_url,
              thumbnail_url: item.thumbnail_url,
              frame_rate: item.fps ?? item.frame_rate,
              keyframes: [],
              best_score: 0,
            };
          }
          grouped[videoId].keyframes.push(item);
          grouped[videoId].best_score = Math.max(grouped[videoId].best_score, item.confidence_score);
        });

        // Sort keyframes within each video by score
        Object.values(grouped).forEach(video => {
          video.keyframes.sort((a, b) => b.confidence_score - a.confidence_score);
        });

        // Convert to sorted video list
        const videoList = Object.values(grouped)
          .sort((a, b) => b.best_score - a.best_score);

        setVideoData(grouped);
        setItems(videoList);
      }
      
    } catch (e) {
      if (e.name === 'AbortError') {
        setError('Search was cancelled');
      } else if (e.message.includes('Failed to fetch')) {
        setError(`Unable to connect to backend at ${BACKEND_ORIGIN}`);
      } else {
        setError(String(e?.message || e));
      }
    } finally {
      setLoading(false);
      setAbortController(null);
    }
  };

  const cancelSearch = () => {
    if (abortController) {
      abortController.abort();
      setAbortController(null);
      setLoading(false);
      setError('Search cancelled by user');
    }
  };

  const latestRef = useRef({});
  latestRef.current = {
    query,
    csvBaseName,
    k,
    scoreThreshold,
    modelName,
    temporalSearch,
    hybridSearch,
    translate,
    batch,
    searchLevel,
    selectedBatches,
    selectedGroups,
    selectedVideos,
    filteringEnabled,
    filterOd,
    filterOcr,
    filterAsr,
    odValues,
    ocrValues,
    asrValues,
    excludeByGroup,
    excludeGroupsText,
    excludeByVideo,
    excludeVidIdsText,
  };

  const collectSearchConfig = () => {
    const s = latestRef.current;
    const listFromValues = (values) =>
      (values || []).map((v) => (v || '').trim()).filter((v) => v.length > 0);
    const liveQuery = document.getElementById('visual-search-query')?.value;
    const liveName = document.getElementById('visual-search-query-name')?.value;
    const queryText = (liveQuery != null && liveQuery !== '') ? liveQuery : (s.query || '');
    const queryName = (liveName != null && liveName !== '') ? liveName : (s.csvBaseName || '');
    const finalK = s.k === '' || s.k === null || s.k === undefined ? 10 : parseInt(s.k, 10) || 10;
    const finalScoreThreshold =
      s.scoreThreshold === '' || s.scoreThreshold === null || s.scoreThreshold === undefined
        ? 0.0
        : parseFloat(s.scoreThreshold) || 0.0;
    const filtering = {};
    if (s.filteringEnabled) {
      if (s.filterOd) filtering.od_text = listFromValues(s.odValues);
      if (s.filterOcr) filtering.ocr_text = listFromValues(s.ocrValues);
      if (s.filterAsr) filtering.asr_text = listFromValues(s.asrValues);
    }
    const searchMethod = s.filteringEnabled
      ? 'filtering'
      : s.temporalSearch
        ? 'temporal'
        : s.hybridSearch
          ? 'hybrid'
          : 'normal';
    const request = {
      query: queryText,
      top_k: finalK,
      score_threshold: finalScoreThreshold,
      model_name: s.modelName,
      search_method: searchMethod,
      translate: Boolean(s.translate),
      filtering: Object.keys(filtering).length > 0 ? filtering : undefined,
      exclude_groups: s.excludeByGroup
        ? (s.excludeGroupsText || '').split(',').map((x) => x.trim()).filter(Boolean)
        : undefined,
      exclude_vid_id: s.excludeByVideo
        ? (s.excludeVidIdsText || '').split(',').map((x) => x.trim()).filter(Boolean)
        : undefined,
    };
    if (s.searchLevel === 'group' && (s.selectedGroups || []).length > 0) {
      request.include_groups = s.selectedGroups;
    }
    if (s.searchLevel === 'video' && (s.selectedVideos || []).length > 0) {
      request.include_videos = s.selectedVideos;
    }
    return {
      version: 1,
      exported_at: new Date().toISOString(),
      query_name: queryName,
      ui: {
        query: queryText,
        query_name: queryName,
        search_level: s.searchLevel,
        selected_batches: s.selectedBatches,
        selected_groups: s.selectedGroups,
        selected_videos: s.selectedVideos,
        temporal_search: s.temporalSearch,
        hybrid_search: s.hybridSearch,
        translate: Boolean(s.translate),
        filtering_enabled: s.filteringEnabled,
        filter_od: s.filterOd,
        filter_ocr: s.filterOcr,
        filter_asr: s.filterAsr,
        od_values: s.odValues,
        ocr_values: s.ocrValues,
        asr_values: s.asrValues,
        exclude_by_group: s.excludeByGroup,
        exclude_groups_text: s.excludeGroupsText,
        exclude_by_video: s.excludeByVideo,
        exclude_vid_ids_text: s.excludeVidIdsText,
        batch: s.batch,
        model_name: s.modelName,
        top_k: finalK,
        score_threshold: finalScoreThreshold,
      },
      request,
    };
  };

  const exportSearchConfig = () => {
    const config = collectSearchConfig();
    const stamp = new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19);
    const rawName = (config.query_name || config.request.query || 'search-config').trim() || 'search-config';
    const safeName = rawName.replace(/[^\w.-]+/g, '_').slice(0, 60);
    downloadJson(config, `${safeName}-${stamp}`);
  };

  const configFileInputRef = useRef(null);

  const applyImportedConfig = (config) => {
    if (!config || typeof config !== 'object') {
      throw new Error('Invalid config: expected a JSON object');
    }
    const ui = (config.ui && typeof config.ui === 'object') ? config.ui : {};
    const request = (config.request && typeof config.request === 'object') ? config.request : {};

    const pick = (...vals) => {
      for (const v of vals) {
        if (v !== undefined && v !== null) return v;
      }
      return undefined;
    };

    const nextQuery = pick(ui.query, request.query, config.query);
    if (typeof nextQuery === 'string') setQuery(nextQuery);

    const nextName = pick(ui.query_name, config.query_name, ui.csv_base_name);
    if (typeof nextName === 'string') setCsvBaseName(nextName);

    const nextK = pick(ui.top_k, request.top_k, ui.k);
    if (nextK !== undefined && nextK !== '') setK(parseInt(nextK, 10) || 10);

    const nextScore = pick(ui.score_threshold, request.score_threshold);
    if (nextScore !== undefined && nextScore !== '') setScoreThreshold(parseFloat(nextScore) || 0);

    const nextModel = pick(ui.model_name, request.model_name);
    if (typeof nextModel === 'string' && nextModel) setModelName(nextModel);

    const nextTemporal = pick(ui.temporal_search, request.search_method === 'temporal');
    if (typeof nextTemporal === 'boolean') setTemporalSearch(nextTemporal);

    const nextHybrid = pick(ui.hybrid_search, request.search_method === 'hybrid');
    if (typeof nextHybrid === 'boolean') {
      setHybridSearch(Boolean(nextHybrid) && nextTemporal !== true);
    } else if (nextTemporal === true) {
      setHybridSearch(false);
    }

    const nextTranslate = pick(ui.translate, request.translate);
    if (typeof nextTranslate === 'boolean') setTranslate(nextTranslate);

    const nextBatch = pick(ui.batch, request.batch);
    if (nextBatch !== undefined && nextBatch !== '') setBatch(parseInt(nextBatch, 10) || 1);

    const nextLevel = pick(ui.search_level);
    if (nextLevel === 'group' || nextLevel === 'video' || nextLevel === 'all') {
      setSearchLevel(nextLevel);
    } else if (nextLevel === 'batch') {
      setSearchLevel('all');
    }

    if (Array.isArray(ui.selected_batches)) setSelectedBatches(ui.selected_batches);
    if (Array.isArray(ui.selected_groups)) setSelectedGroups(ui.selected_groups);
    else if (Array.isArray(request.include_groups)) setSelectedGroups(request.include_groups);
    if (Array.isArray(ui.selected_videos)) setSelectedVideos(ui.selected_videos);
    else if (Array.isArray(request.include_videos)) setSelectedVideos(request.include_videos);

    const nextFiltering = pick(ui.filtering_enabled, Boolean(request.filtering));
    if (typeof nextFiltering === 'boolean') setFilteringEnabled(nextFiltering);

    if (typeof ui.filter_od === 'boolean') setFilterOd(ui.filter_od);
    else if (request.filtering?.od_text) setFilterOd(true);

    if (typeof ui.filter_ocr === 'boolean') setFilterOcr(ui.filter_ocr);
    else if (request.filtering?.ocr_text) setFilterOcr(true);

    if (typeof ui.filter_asr === 'boolean') setFilterAsr(ui.filter_asr);
    else if (request.filtering?.asr_text) setFilterAsr(true);

    const asValues = (v) => {
      if (Array.isArray(v) && v.length > 0) return v.map((x) => String(x ?? ''));
      return [''];
    };
    if (ui.od_values) setOdValues(asValues(ui.od_values));
    else if (request.filtering?.od_text) setOdValues(asValues(request.filtering.od_text));
    if (ui.ocr_values) setOcrValues(asValues(ui.ocr_values));
    else if (request.filtering?.ocr_text) setOcrValues(asValues(request.filtering.ocr_text));
    if (ui.asr_values) setAsrValues(asValues(ui.asr_values));
    else if (request.filtering?.asr_text) setAsrValues(asValues(request.filtering.asr_text));

    if (typeof ui.exclude_by_group === 'boolean') setExcludeByGroup(ui.exclude_by_group);
    else if (Array.isArray(request.exclude_groups)) setExcludeByGroup(request.exclude_groups.length > 0);

    if (typeof ui.exclude_groups_text === 'string') setExcludeGroupsText(ui.exclude_groups_text);
    else if (Array.isArray(request.exclude_groups)) setExcludeGroupsText(request.exclude_groups.join(','));

    if (typeof ui.exclude_by_video === 'boolean') setExcludeByVideo(ui.exclude_by_video);
    else if (Array.isArray(request.exclude_vid_id)) setExcludeByVideo(request.exclude_vid_id.length > 0);

    if (typeof ui.exclude_vid_ids_text === 'string') setExcludeVidIdsText(ui.exclude_vid_ids_text);
    else if (Array.isArray(request.exclude_vid_id)) setExcludeVidIdsText(request.exclude_vid_id.join(','));

    setError('');
  };

  const importSearchConfig = (event) => {
    const file = event.target.files?.[0];
    event.target.value = '';
    if (!file) return;
    const reader = new FileReader();
    reader.onload = () => {
      try {
        const text = String(reader.result || '');
        const parsed = JSON.parse(text);
        applyImportedConfig(parsed);
      } catch (e) {
        setError(`Import config failed: ${e?.message || e}`);
      }
    };
    reader.onerror = () => setError('Import config failed: could not read file');
    reader.readAsText(file);
  };

  const filteredItems = useMemo(
    () => (items || []).filter((video) => matchResultKeyword(video, resultKeyword)),
    [items, resultKeyword]
  );

  const filteredVideoData = useMemo(() => {
    if (!(resultKeyword || '').trim()) return videoData;
    const next = {};
    Object.entries(videoData || {}).forEach(([videoId, video]) => {
      if (matchResultKeyword(video, resultKeyword)) {
        next[videoId] = video;
      }
    });
    return next;
  }, [videoData, resultKeyword]);

  const exploreVideo = (videoId) => {
    try {
      const y = window.pageYOffset || document.documentElement.scrollTop || 0;
      setVideosScrollY(y);
    } catch (_) {}
    setSelectedVideoId(videoId);
    setVideoView('keyframes');
  };

  const backToVideos = () => {
    setVideoView('list');
    setSelectedVideoId(null);
    setTimeout(() => {
      try {
        window.scrollTo(0, videosScrollY || 0);
      } catch (_) {}
    }, 0);
  };

  const buildFilteringPayload = () => {
    // origin_paths: take all unique video_ids from the first response
    const originPaths = Array.from(new Set(Object.keys(videoData)));

    // Convert value arrays to list[str], trimming empties
    const listFromValues = (values) =>
      (values || []).map(v => (v || '').trim()).filter(v => v.length > 0);

    const filtering = {};
    if (filterOd) filtering.od_text = listFromValues(odValues);
    if (filterOcr) filtering.ocr_text = listFromValues(ocrValues);
    if (filterAsr) filtering.asr_text = listFromValues(asrValues);

    return { origin_paths: originPaths, filtering };
  };

  const onFilter = async () => {
    try {
      setError("");
      if (!filteringEnabled) return;

      const payload = buildFilteringPayload();
      if (!payload.origin_paths || payload.origin_paths.length === 0) {
        setError('No origin paths available. Run a search first.');
        return;
      }
      if (!payload.filtering || Object.keys(payload.filtering).length === 0) {
        setError('Select at least one filtering type and add values.');
        return;
      }

      const res = await fetch(apiUrl('/filter-search'), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}: ${res.statusText}`);
      const data = await res.json();

      // Assume backend returns a list of filtered paths (video_ids)
      const filteredPaths = Array.isArray(data)
        ? data
        : (data.results || data.paths || data.origin_paths || []);
      if (!Array.isArray(filteredPaths)) {
        setError('Unexpected filtering response format');
        return;
      }

      const allowed = new Set(filteredPaths);
      const newVideoData = {};
      Object.entries(videoData).forEach(([vid, v]) => {
        if (allowed.has(vid)) newVideoData[vid] = v;
      });
      const newItems = (items || []).filter(v => allowed.has(v.video_id));

      setVideoData(newVideoData);
      setItems(newItems);

      // If current selected video is filtered out, reset view
      if (selectedVideoId && !allowed.has(selectedVideoId)) {
        backToVideos();
      }
    } catch (e) {
      setError(String(e?.message || e));
    }
  };

  const renderValues = (values, setValues) => (
    <div className="filter-pairs">
      {values.map((val, idx) => (
        <div className="filter-pair-row" key={idx}>
          <input
            type="text"
            placeholder="value"
            value={val}
            onChange={(e) => {
              const next = [...values];
              next[idx] = e.target.value;
              setValues(next);
            }}
          />
          <button
            className="small-btn"
            onClick={() => setValues(values.filter((_, i) => i !== idx))}
            disabled={values.length <= 1}
          >
            −
          </button>
          <button
            className="small-btn"
            onClick={() => setValues([...values, ""])}
          >
            +
          </button>
        </div>
      ))}
    </div>
  );

  const trimFilterValues = (values) =>
    (values || []).map((v) => (v || '').trim()).filter((v) => v.length > 0);

  const removeFilterValue = (kind, value) => {
    const strip = (list, setList) => {
      const next = list.filter((v) => (v || '').trim() !== value);
      setList(next.length > 0 ? next : ['']);
    };
    if (kind === 'od') strip(odValues, setOdValues);
    if (kind === 'ocr') strip(ocrValues, setOcrValues);
    if (kind === 'asr') strip(asrValues, setAsrValues);
  };

  const activeFilterChips = useMemo(() => {
    const chips = [];
    if (filteringEnabled && filterOd) {
      trimFilterValues(odValues).forEach((v) => chips.push({ kind: 'od', label: 'OD', value: v }));
    }
    if (filteringEnabled && filterOcr) {
      trimFilterValues(ocrValues).forEach((v) => chips.push({ kind: 'ocr', label: 'OCR', value: v }));
    }
    if (filteringEnabled && filterAsr) {
      trimFilterValues(asrValues).forEach((v) => chips.push({ kind: 'asr', label: 'ASR', value: v }));
    }
    return chips;
  }, [filteringEnabled, filterOd, filterOcr, filterAsr, odValues, ocrValues, asrValues]);

  const activeFilterModes = useMemo(() => {
    if (!filteringEnabled) return [];
    const modes = [];
    if (filterOd) modes.push('OD');
    if (filterOcr) modes.push('OCR');
    if (filterAsr) modes.push('ASR');
    return modes;
  }, [filteringEnabled, filterOd, filterOcr, filterAsr]);

  return (
    <div className="layout">
      <ResizableSidebar>
        <div className="sidebar-title-row">
          <h2 className="sidebar-title">Controls</h2>
          <div className="sidebar-config-actions">
            <button
              type="button"
              className="import-config-btn"
              onClick={() => configFileInputRef.current?.click()}
              title="Load search settings from a JSON config file"
            >
              Import config
            </button>
            <button
              type="button"
              className="export-config-btn"
              onClick={exportSearchConfig}
              title="Download current search settings as JSON"
            >
              Export config
            </button>
            <input
              ref={configFileInputRef}
              type="file"
              accept="application/json,.json"
              style={{ display: 'none' }}
              onChange={importSearchConfig}
            />
          </div>
        </div>
        
        <SearchLevelSelector
          searchLevel={searchLevel}
          setSearchLevel={setSearchLevel}
          selectedGroups={selectedGroups}
          setSelectedGroups={setSelectedGroups}
          selectedVideos={selectedVideos}
          setSelectedVideos={setSelectedVideos}
        />
        
        <SearchForm
          query={query}
          setQuery={setQuery}
          csvBaseName={csvBaseName}
          setCsvBaseName={setCsvBaseName}
          k={k}
          setK={setK}
          scoreThreshold={scoreThreshold}
          setScoreThreshold={setScoreThreshold}
          modelName={modelName}
          setModelName={setModelName}
          temporalSearch={temporalSearch}
          setTemporalSearch={setTemporalSearch}
          hybridSearch={hybridSearch}
          setHybridSearch={setHybridSearch}
          translate={translate}
          setTranslate={setTranslate}
          loading={loading}
          onSearch={doSearch}
          onCancel={cancelSearch}
        />

        {/* Filtering controls */}
        <div className="controls">
          <div className={`filter-block${filteringEnabled ? ' filter-block--on' : ''}`}>
            <div className="filter-block-header">
              <div className="filter-block-title">
                <span>Post-filters</span>
                <span className={`filter-mode-badge${filteringEnabled ? ' is-on' : ''}`}>
                  {filteringEnabled ? 'ON' : 'OFF'}
                </span>
              </div>
              <button
                type="button"
                className={`filter-master-toggle${filteringEnabled ? ' is-on' : ''}`}
                onClick={() => setFilteringEnabled((v) => !v)}
                aria-pressed={filteringEnabled}
              >
                {filteringEnabled ? 'Disable' : 'Enable'}
              </button>
            </div>

            {filteringEnabled && (
              <>
                <div className="filter-mode-row" role="group" aria-label="Filter modes">
                  <button
                    type="button"
                    className={`filter-mode-pill filter-mode-pill--od${filterOd ? ' is-active' : ''}`}
                    onClick={() => setFilterOd((v) => !v)}
                    aria-pressed={filterOd}
                  >
                    OD
                  </button>
                  <button
                    type="button"
                    className={`filter-mode-pill filter-mode-pill--ocr${filterOcr ? ' is-active' : ''}`}
                    onClick={() => setFilterOcr((v) => !v)}
                    aria-pressed={filterOcr}
                  >
                    OCR
                  </button>
                  <button
                    type="button"
                    className={`filter-mode-pill filter-mode-pill--asr${filterAsr ? ' is-active' : ''}`}
                    onClick={() => setFilterAsr((v) => !v)}
                    aria-pressed={filterAsr}
                  >
                    ASR
                  </button>
                </div>

                {(activeFilterModes.length > 0 || activeFilterChips.length > 0) && (
                  <div className="filter-active-summary">
                    <div className="filter-active-label">
                      Active: {activeFilterModes.length ? activeFilterModes.join(' · ') : 'none'}
                    </div>
                    {activeFilterChips.length > 0 ? (
                      <div className="filter-chip-row">
                        {activeFilterChips.map((chip) => (
                          <button
                            key={`${chip.kind}-${chip.value}`}
                            type="button"
                            className={`filter-chip filter-chip--${chip.kind}`}
                            onClick={() => removeFilterValue(chip.kind, chip.value)}
                            title={`Remove ${chip.label} filter`}
                          >
                            <span className="filter-chip-kind">{chip.label}</span>
                            <span className="filter-chip-value">{chip.value}</span>
                            <span className="filter-chip-x" aria-hidden="true">×</span>
                          </button>
                        ))}
                      </div>
                    ) : (
                      <div className="filter-active-hint">Add values below, then click Apply filter</div>
                    )}
                  </div>
                )}

                {filterOd && (
                  <div className="filter-section filter-section--od">
                    <div className="filter-title">OD values</div>
                    {renderValues(odValues, setOdValues)}
                  </div>
                )}
                {filterOcr && (
                  <div className="filter-section filter-section--ocr">
                    <div className="filter-title">OCR values</div>
                    {renderValues(ocrValues, setOcrValues)}
                  </div>
                )}
                {filterAsr && (
                  <div className="filter-section filter-section--asr">
                    <div className="filter-title">ASR values</div>
                    {renderValues(asrValues, setAsrValues)}
                  </div>
                )}

                <div className="filter-apply-row">
                  <button
                    type="button"
                    className="filter-apply-btn"
                    onClick={onFilter}
                    disabled={!activeFilterModes.length}
                  >
                    Apply filter
                  </button>
                </div>
              </>
            )}
          </div>

          {/* Exclusion controls */}
          <div className="control-group" style={{ marginTop: '12px' }}>
            <label>
              <input
                type="checkbox"
                checked={excludeByGroup}
                onChange={(e) => setExcludeByGroup(e.target.checked)}
              />
              exclude by group
            </label>
            {excludeByGroup && (
              <input
                type="text"
                placeholder="e.g. L21,L22"
                value={excludeGroupsText}
                onChange={(e) => setExcludeGroupsText(e.target.value)}
                style={{ marginTop: '6px' }}
              />
            )}
          </div>

          <div className="control-group">
            <label>
              <input
                type="checkbox"
                checked={excludeByVideo}
                onChange={(e) => setExcludeByVideo(e.target.checked)}
              />
              exclude by video id
            </label>
            {excludeByVideo && (
              <input
                type="text"
                placeholder="e.g. L21_V001,L22_V003"
                value={excludeVidIdsText}
                onChange={(e) => setExcludeVidIdsText(e.target.value)}
                style={{ marginTop: '6px' }}
              />
            )}
          </div>
        </div>
      </ResizableSidebar>

      <main className="content">
        <div className="container">
          {/* Tab Navigation */}
          <div className="tab-navigation">
            <a
              href={pathForTab('visual')}
              className={`tab-button ${activeTab === 'visual' ? 'active' : ''}`}
              onClick={(e) => {
                e.preventDefault();
                setActiveTab('visual');
              }}
            >
              Visual Search
            </a>
            <a
              href={pathForTab('asr')}
              className={`tab-button ${activeTab === 'asr' ? 'active' : ''}`}
              onClick={(e) => {
                e.preventDefault();
                setActiveTab('asr');
              }}
            >
              ASR Search
            </a>
            <a
              href={pathForTab('od')}
              className={`tab-button ${activeTab === 'od' ? 'active' : ''}`}
              onClick={(e) => {
                e.preventDefault();
                setActiveTab('od');
              }}
            >
              OD Search
            </a>
            <a
              href={pathForTab('ocr')}
              className={`tab-button ${activeTab === 'ocr' ? 'active' : ''}`}
              onClick={(e) => {
                e.preventDefault();
                setActiveTab('ocr');
              }}
            >
              OCR Search
            </a>
            <a
              href={pathForTab('cctv')}
              className={`tab-button ${activeTab === 'cctv' ? 'active' : ''}`}
              onClick={(e) => {
                e.preventDefault();
                setActiveTab('cctv');
              }}
            >
              CCTV
            </a>
            <a
              href={pathForTab('shot')}
              className={`tab-button ${activeTab === 'shot' ? 'active' : ''}`}
              onClick={(e) => {
                e.preventDefault();
                setActiveTab('shot');
              }}
            >
              Ảnh đề
            </a>
            <a
              href={pathForTab('voice')}
              className={`tab-button ${activeTab === 'voice' ? 'active' : ''}`}
              onClick={(e) => {
                e.preventDefault();
                setActiveTab('voice');
              }}
            >
              Giọng nói
            </a>
          </div>

          <div className={`tab-panel${activeTab !== 'visual' ? ' tab-panel--hidden' : ''}`}>
              <h1>Visual Search</h1>

          {/* Top toolbar with View Mode and Sort together */}
          {!loading && items.length > 0 && (
            <div className="top-toolbar">
              <div className="view-mode-selector">
                <label>View Mode:</label>
                <select value={viewMode} onChange={(e) => setViewMode(e.target.value)}>
                  <option value="videos">Videos</option>
                  <option value="frames">Frames</option>
                </select>
              </div>
              <div className="result-filter">
                <label htmlFor="result-keyword">Filter results:</label>
                <input
                  id="result-keyword"
                  type="search"
                  value={resultKeyword}
                  onChange={(e) => setResultKeyword(e.target.value)}
                  placeholder="Search video id / group / keyword in results…"
                />
                {resultKeyword.trim() && (
                  <button
                    type="button"
                    className="result-filter-clear"
                    onClick={() => setResultKeyword('')}
                    title="Clear filter"
                  >
                    Clear
                  </button>
                )}
                <span className="result-filter-count">
                  {viewMode === 'videos'
                    ? `${filteredItems.length}/${items.length} videos`
                    : `${Object.keys(filteredVideoData).length}/${Object.keys(videoData).length} videos`}
                </span>
              </div>
              <div className="sort-controls compact">
                {viewMode === 'videos' && videoView === 'list' ? (
                  <>
                    <label>Sort videos by:</label>
                    <select value={videoListSortBy} onChange={(e) => setVideoListSortBy(e.target.value)}>
                      <option value="score">Best Score</option>
                      <option value="name">Video Name</option>
                      <option value="frames">Number of Frames</option>
                    </select>
                  </>
                ) : (
                  <>
                    <label>Sort frames by:</label>
                    <select value={frameSortBy} onChange={(e) => setFrameSortBy(e.target.value)}>
                      <option value="score">Best Score</option>
                      <option value="name">Frame Number</option>
                      <option value="time">Timestamp</option>
                      <option value="video">Video Name</option>
                    </select>
                  </>
                )}
              </div>
            </div>
          )}

          {filteringEnabled && (activeFilterModes.length > 0 || activeFilterChips.length > 0) && (
            <div className="active-filters-bar" aria-live="polite">
              <span className="active-filters-bar-label">
                Filters {activeFilterModes.length ? activeFilterModes.join(' · ') : 'ready'}
              </span>
              <div className="filter-chip-row">
                {activeFilterChips.length > 0 ? (
                  activeFilterChips.map((chip) => (
                    <button
                      key={`bar-${chip.kind}-${chip.value}`}
                      type="button"
                      className={`filter-chip filter-chip--${chip.kind}`}
                      onClick={() => removeFilterValue(chip.kind, chip.value)}
                      title={`Remove ${chip.label} filter`}
                    >
                      <span className="filter-chip-kind">{chip.label}</span>
                      <span className="filter-chip-value">{chip.value}</span>
                      <span className="filter-chip-x" aria-hidden="true">×</span>
                    </button>
                  ))
                ) : (
                  <span className="filter-active-hint">Modes on — add values in Controls</span>
                )}
              </div>
            </div>
          )}

          {error && <div className="status">Error: {error}</div>}
          {!error && loading && <div className="status">Searching...</div>}
          {!error && !loading && temporalSearch && (
            <div className="temporal-status">
              🔄 Temporal Search Mode: Breaking down query into sequential events
            </div>
          )}
          {!error && !loading && hybridSearch && !temporalSearch && (
            <div className="temporal-status">
              Hybrid mode: visual retrieval + enrichment FTS rerank
            </div>
          )}
          {!error && !loading && processingTime && (
            <div className="processing-time">
              ⚡ Search completed in {processingTime.toFixed(2)} seconds
              {searchLevel !== 'all' && (
                <span className="search-method-indicator">
                  {' '}• Hierarchical Search ({searchLevel} level)
                </span>
              )}
            </div>
          )}
          
          {/* Main content area */}
          {viewMode === 'videos' ? (
            // Videos view - normal behavior
            videoView === 'list' ? (
              filteredItems.length > 0 ? (
                <VideoList 
                  items={filteredItems}
                  rankItems={items}
                  onExploreVideo={exploreVideo}
                  sortBy={videoListSortBy}
                />
              ) : items.length > 0 ? (
                <div className="status">No videos match “{resultKeyword.trim()}”</div>
              ) : null
            ) : (
              <KeyframeGrid
                selectedVideoId={selectedVideoId}
                videoData={filteredVideoData}
                onBackToVideos={backToVideos}
                onOpenVideo={onOpenVideo}
                csvBaseName={csvBaseName}
                sortBy={frameSortBy}
              />
            )
          ) : (
            // Frames view - show all frames from all videos
            Object.keys(filteredVideoData).length > 0 ? (
              <AllFramesView 
                videoData={filteredVideoData}
                rankVideoData={videoData}
                onOpenVideo={onOpenVideo}
                csvBaseName={csvBaseName}
                sortBy={frameSortBy}
              />
            ) : Object.keys(videoData).length > 0 ? (
              <div className="status">No frames match “{resultKeyword.trim()}”</div>
            ) : null
          )}
          </div>

          <div className={`tab-panel${activeTab !== 'asr' ? ' tab-panel--hidden' : ''}`}>
            <ASRSearchTab />
          </div>

          <div className={`tab-panel${activeTab !== 'od' ? ' tab-panel--hidden' : ''}`}>
            <ODSearchTab />
          </div>

          <div className={`tab-panel${activeTab !== 'ocr' ? ' tab-panel--hidden' : ''}`}>
            <OCRSearchTab />
          </div>

          <div className={`tab-panel${activeTab !== 'cctv' ? ' tab-panel--hidden' : ''}`}>
            <CCTVTab />
          </div>

          <div className={`tab-panel${activeTab !== 'shot' ? ' tab-panel--hidden' : ''}`}>
            <ShotOcrTab
              active={activeTab === 'shot'}
              onUseQuery={(nextQuery) => {
                setQuery(nextQuery);
                setActiveTab('visual');
                doSearch(nextQuery);
              }}
            />
          </div>

          <div className={`tab-panel${activeTab !== 'voice' ? ' tab-panel--hidden' : ''}`}>
            <VoiceAsrTab
              active={activeTab === 'voice'}
              onUseQuery={(nextQuery) => {
                setQuery(nextQuery);
                setActiveTab('visual');
                doSearch(nextQuery);
              }}
            />
          </div>
        </div>
      </main>
    </div>
  );
};

export default SearchInterface;
