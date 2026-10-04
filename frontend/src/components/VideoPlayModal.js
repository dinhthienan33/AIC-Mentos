// src/components/VideoPlayerModal.jsx
import React, { useEffect, useMemo, useRef, useState } from 'react';
import { frameToMs, resolveFps, timestampToFrameNumber } from '../utils/fpsUtils';
import { buildShot } from '../dres/format';
import { useDres } from '../dres/DresContext';

const isYouTubeHost = (h) => /(^|\.)youtube\.com$|(^|\.)youtu\.be$/.test(h);

const VideoPlayerModal = ({
    videoUrl,
    startSeconds = 0,
    markers = [],
    keyframeRefs = [],     // mảng keyframe model trả về (có timestamp, keyframe_num, ...)
    onClose,
    videoId,
    csvBaseName,
    frameRate,
    embedded = false,
}) => {
    // === Refs & state
    const ytDivRef = useRef(null);
    const ytPlayerRef = useRef(null);
    const htmlVideoRef = useRef(null);

    const [apiReady, setApiReady] = useState(!!window.YT?.Player);
    const [ytReady, setYtReady] = useState(false);
    const [duration, setDuration] = useState(0);
    const [currentIndex, setCurrentIndex] = useState(0);
    const [currentSec, setCurrentSec] = useState(Math.max(0, Math.floor(startSeconds || 0)));
    const [copyMessage, setCopyMessage] = useState('');
    const dres = useDres();
    
    // marks hiển thị trên thanh marker
    const sortedMarkers = useMemo(
        () =>
            Array.from(new Set((markers || []).map(n => Math.max(0, Math.floor(n))))).sort((a, b) => a - b),
        [markers]
    );

    // danh sách pick xuất CSV (đúng format "videoId,frameIdx")
    const [picked, setPicked] = useState([]); // [{ line, sec, frameIdx }]

    // === helpers
    const formatTime = (s) => {
        const sec = Math.max(0, Math.floor(s || 0));
        const h = Math.floor(sec / 3600);
        const m = Math.floor((sec % 3600) / 60);
        const ss = sec % 60;
        return h > 0
            ? `${h}:${String(m).padStart(2, '0')}:${String(ss).padStart(2, '0')}`
            : `${m}:${String(ss).padStart(2, '0')}`;
    };

    const isYouTube = useMemo(() => {
        try {
            const u = new URL(videoUrl);
            return isYouTubeHost(u.hostname.replace(/^www\./, ''));
        } catch {
            return false;
        }
    }, [videoUrl]);

    const isBunnyEmbed = useMemo(() => {
        try {
            return new URL(videoUrl).hostname.endsWith('mediadelivery.net');
        } catch {
            return false;
        }
    }, [videoUrl]);

    const [bunnySeek, setBunnySeek] = useState(null);
    const bunnySrc = useMemo(() => {
        if (!isBunnyEmbed) return videoUrl;
        try {
            const u = new URL(videoUrl);
            const t = bunnySeek == null ? Math.max(0, Math.floor(startSeconds || 0)) : bunnySeek;
            u.searchParams.set('t', String(t));
            u.searchParams.set('autoplay', 'true');
            return u.toString();
        } catch {
            return videoUrl;
        }
    }, [isBunnyEmbed, videoUrl, startSeconds, bunnySeek]);

    const getCurrentTime = () => {
        if (isYouTube && ytReady && ytPlayerRef.current?.getCurrentTime) {
            return Number(ytPlayerRef.current.getCurrentTime() || 0);
        }
        if (htmlVideoRef.current) return Number(htmlVideoRef.current.currentTime || 0);
        return 0;
    };

    const playbackSecRef = useRef(Math.max(0, Math.floor(startSeconds || 0)));

    const seekTo = (sec) => {
        const t = Math.max(0, Math.floor(sec || 0));
        playbackSecRef.current = t;
        setCurrentSec(t);
        if (isYouTube && ytReady && ytPlayerRef.current && typeof ytPlayerRef.current.seekTo === 'function') {
            ytPlayerRef.current.seekTo(t, true);
            ytPlayerRef.current.playVideo?.();
        } else if (isBunnyEmbed) {
            setBunnySeek(t);
        } else if (htmlVideoRef.current) {
            htmlVideoRef.current.currentTime = t;
            htmlVideoRef.current.play().catch(() => { });
        }
    };

    // === Load YT Iframe API (nếu là Youtube)
    useEffect(() => {
        if (!isYouTube) return;
        if (window.YT?.Player) { setApiReady(true); return; }
        let script = document.querySelector('script[data-yt-api]');
        if (!script) {
            script = document.createElement('script');
            script.src = 'https://www.youtube.com/iframe_api';
            script.async = true;
            script.dataset.ytApi = '1';
            document.body.appendChild(script);
        }
        const prev = window.onYouTubeIframeAPIReady;
        window.onYouTubeIframeAPIReady = () => { prev?.(); setApiReady(true); };
    }, [isYouTube]);

    // === ESC to close & lock scroll
    useEffect(() => {
        const original = document.body.style.overflow;
        document.body.style.overflow = 'hidden';
        const onKey = (e) => { if (e.key === 'Escape') onClose?.(); };
        window.addEventListener('keydown', onKey);
        return () => {
            document.body.style.overflow = original;
            window.removeEventListener('keydown', onKey);
        };
    }, [onClose]);

    // === Tạo player YouTube
    const createdRef = useRef(false);
    useEffect(() => {
        if (!isYouTube || !apiReady || !ytDivRef.current || createdRef.current) return;
        // Lấy videoId từ URL
        let id = null;
        try {
            const u = new URL(videoUrl);
            if (u.hostname.includes('youtu.be')) {
                id = u.pathname.split('/').filter(Boolean)[0];
            } else if (u.hostname.includes('youtube.com')) {
                id = u.searchParams.get('v') || (u.pathname.startsWith('/shorts/') ? u.pathname.split('/')[2] : null);
            }
        } catch { }
        if (!id) return;

        try { ytPlayerRef.current?.destroy?.(); } catch { }

        ytPlayerRef.current = new window.YT.Player(ytDivRef.current, {
            videoId: id,
            playerVars: {
                autoplay: 1, controls: 1, rel: 0, modestbranding: 1,
                start: Math.max(0, Math.floor(startSeconds || 0)),
            },
            events: {
                onReady: (e) => {
                    ytPlayerRef.current = e.target;
                    createdRef.current = true;
                    setYtReady(true);
                    const s = Math.max(0, Math.floor(startSeconds || 0));
                    e.target.seekTo(s, true);
                    e.target.playVideo?.();
                }
            }
        });

        return () => { try { ytPlayerRef.current?.destroy?.(); } catch { } };
    }, [isYouTube, apiReady, videoUrl, startSeconds]);

    // === Khi đổi startSeconds, tua tới
    useEffect(() => {
        const s = Math.max(0, Math.floor(startSeconds || 0));
        playbackSecRef.current = s;
        setCurrentSec(s);
        if (isYouTube && ytReady && ytPlayerRef.current && typeof ytPlayerRef.current.seekTo === 'function') {
            ytPlayerRef.current.seekTo(s, true);
            ytPlayerRef.current.playVideo?.();
        } else if (htmlVideoRef.current) {
            htmlVideoRef.current.currentTime = s;
            htmlVideoRef.current.play().catch(() => { });
        }
    }, [startSeconds, isYouTube, ytReady]);

    // === Lấy duration
    useEffect(() => {
        if (isYouTube && ytPlayerRef.current) {
            const id = setInterval(() => {
                const d = Number(ytPlayerRef.current.getDuration?.() || 0);
                if (d > 0) { setDuration(d); clearInterval(id); }
            }, 200);
            return () => clearInterval(id);
        }
    }, [isYouTube, apiReady]);

    useEffect(() => {
        // chỉ poll khi player đã ready
        if (!(ytReady || htmlVideoRef.current)) return;
        let id = setInterval(() => {
            const live = getCurrentTime();
            if (!isBunnyEmbed && live > 0) playbackSecRef.current = live;
            setCurrentSec(isBunnyEmbed ? playbackSecRef.current : live);
        }, 250); // 4Hz là mượt đủ
        return () => clearInterval(id);
    }, [ytReady, isBunnyEmbed]);

    const nudgeSeconds = (delta) => {
        const base = isBunnyEmbed ? playbackSecRef.current : getCurrentTime();
        const next = Math.max(0, base + delta);
        seekTo(duration > 0 ? Math.min(duration, next) : next);
    };

    // === Next marker
    const jumpNext = () => {
        if (!sortedMarkers.length) return;
        const nextIdx = (currentIndex + 1) % sortedMarkers.length;
        setCurrentIndex(nextIdx);
        seekTo(sortedMarkers[nextIdx]);
    };

    // === Previous marker
    const jumpPrevious = () => {
        if (!sortedMarkers.length) return;
        const prevIdx = currentIndex === 0 ? sortedMarkers.length - 1 : currentIndex - 1;
        setCurrentIndex(prevIdx);
        seekTo(sortedMarkers[prevIdx]);
    };

    // === Map thời điểm -> keyframe của model (midpoint logic)
    const pickModelKeyframeAtTime = useMemo(() => {
        const refs = Array.from(keyframeRefs || [])
            .filter(k => Number.isFinite(k?.timestamp))
            .sort((a, b) => a.timestamp - b.timestamp);
        if (!refs.length) return () => null;
        const mids = refs.map((kf, i) =>
            i < refs.length - 1 ? (kf.timestamp + refs[i + 1].timestamp) / 2 : Infinity
        );
        return (tSec) => {
            const t = Math.max(0, Number(tSec) || 0);
            for (let i = 0; i < refs.length; i++) {
                if (t < mids[i]) return refs[i];
            }
            return refs[refs.length - 1];
        };
    }, [keyframeRefs]);

    // Suy ra FPS từ các keyframe của model
    const inferredFps = useMemo(() => {
        // lấy các mốc hợp lệ (có timestamp & keyframe_num)
        const refs = Array.from(keyframeRefs || [])
            .filter(k => Number.isFinite(k?.timestamp) && Number.isFinite(k?.keyframe_num))
            .sort((a, b) => a.timestamp - b.timestamp);

        if (refs.length < 2) return undefined;

        // tính fps vi phân: Δframe / Δtime giữa các mốc liên tiếp
        const diffs = [];
        for (let i = 1; i < refs.length; i++) {
            const dt = refs[i].timestamp - refs[i - 1].timestamp;
            const df = refs[i].keyframe_num - refs[i - 1].keyframe_num;
            if (dt > 0 && Number.isFinite(df)) {
                diffs.push(df / dt);
            }
        }
        if (!diffs.length) return undefined;

        // median chống nhiễu
        diffs.sort((a, b) => a - b);
        const mid = Math.floor(diffs.length / 2);
        const median = diffs.length % 2 ? diffs[mid] : (diffs[mid - 1] + diffs[mid]) / 2;

        // snap về các fps phổ biến (ưu tiên 25 vs 30 như yêu cầu)
        const candidates = [25, 30, 24, 50, 60];
        let best = candidates[0], bestDiff = Math.abs(median - candidates[0]);
        for (const c of candidates) {
            const d = Math.abs(median - c);
            if (d < bestDiff) { best = c; bestDiff = d; }
        }
        return best;
    }, [keyframeRefs]);

    const activeFps = useMemo(
        () => resolveFps(frameRate, ...(keyframeRefs || [])) ?? inferredFps ?? 30,
        [frameRate, keyframeRefs, inferredFps]
    );
    const strictFps = useMemo(
        () => resolveFps(frameRate, ...(keyframeRefs || [])),
        [frameRate, keyframeRefs]
    );
    const playheadFrame = strictFps ? Math.floor(currentSec * strictFps) : null;
    const playheadMs = frameToMs(playheadFrame, strictFps);

    const submitPlayhead = () => {
        const t = isBunnyEmbed ? playbackSecRef.current : getCurrentTime();
        dres.submitShot(buildShot({
            videoId,
            frame: strictFps ? Math.floor(t * strictFps) : null,
            fps: strictFps,
            source: 'player',
        }));
    };

    const sendPickedAsTrake = () => {
        if (!strictFps) return;
        dres.replaceTrakeShots(picked.map((item) => buildShot({
            videoId,
            frame: item.frameIdx,
            fps: strictFps,
            source: 'player-list',
        })));
    };

    // === Add keyframe (theo model)
    const addKeyframe = () => {
        const t = getCurrentTime();
        const fpsToUse = activeFps;
        const frameIdx = Math.floor(t * fpsToUse);  // công thức của bạn

        const id = (videoId && String(videoId)) || 'video';
        const line = `${id},${frameIdx}`;

        setPicked(prev => (
            prev.some(p => p.line === line) ? prev : [...prev, { line, sec: Math.floor(t), frameIdx }]
        ));

        console.log(`Added keyframe: ${line} (FPS: ${fpsToUse}, Time: ${t.toFixed(2)}s)`);
        // KHÔNG thêm marker mới lên thanh — giữ nguyên markers mặc định
        // (nên bỏ mọi setCustomMarks trong addKeyframe)
    };


    // === Drag & delete cho picked list
    const onDragStartPicked = (i) => (e) => {
        e.dataTransfer.setData('text/plain', String(i));
        e.dataTransfer.effectAllowed = 'move';
    };
    const onDropPicked = (i) => (e) => {
        e.preventDefault();
        const from = Number(e.dataTransfer.getData('text/plain'));
        if (Number.isNaN(from) || from === i) return;
        setPicked(prev => {
            const next = [...prev];
            const [item] = next.splice(from, 1);
            next.splice(i, 0, item);
            return next;
        });
    };
    const removePicked = (i) => setPicked(prev => prev.filter((_, idx) => idx !== i));

    // === Download 1 item CSV
    const downloadOneCSV = (line) => {
        const blob = new Blob([line], { type: 'text/csv;charset=utf-8;' });
        const a = document.createElement('a');
        a.href = URL.createObjectURL(blob);
        const base = (csvBaseName && csvBaseName.trim()) || 'keyframe';
        a.download = `${base}-${line.replace(/[^a-zA-Z0-9_-]/g, '_')}.csv`;
        a.style.visibility = 'hidden';
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
    };

    // === Copy keyframe to clipboard
    const copyKeyframe = async (line) => {
        try {
            await navigator.clipboard.writeText(line);
            console.log(`Copied to clipboard: ${line}`);
            setCopyMessage('Copied!');
            setTimeout(() => setCopyMessage(''), 2000);
        } catch (err) {
            console.error('Failed to copy: ', err);
            // Fallback for older browsers
            const textArea = document.createElement('textarea');
            textArea.value = line;
            document.body.appendChild(textArea);
            textArea.select();
            document.execCommand('copy');
            document.body.removeChild(textArea);
            setCopyMessage('Copied!');
            setTimeout(() => setCopyMessage(''), 2000);
        }
    };

    // === Copy all keyframes to clipboard
    const copyAllKeyframes = async () => {
        if (picked.length === 0) return;
        
        const allKeyframes = picked.map(p => p.line).join('\n');
        try {
            await navigator.clipboard.writeText(allKeyframes);
            console.log(`Copied ${picked.length} keyframes to clipboard`);
            setCopyMessage(`Copied ${picked.length} keyframes!`);
            setTimeout(() => setCopyMessage(''), 2000);
        } catch (err) {
            console.error('Failed to copy all: ', err);
            // Fallback for older browsers
            const textArea = document.createElement('textarea');
            textArea.value = allKeyframes;
            document.body.appendChild(textArea);
            textArea.select();
            document.execCommand('copy');
            document.body.removeChild(textArea);
            setCopyMessage(`Copied ${picked.length} keyframes!`);
            setTimeout(() => setCopyMessage(''), 2000);
        }
    };

    // === UI
    return (
        <div className={embedded ? 'player-embedded' : 'player-overlay'} onClick={embedded ? undefined : onClose}>
            <div
                className={`player-container${embedded ? ' player-container--embedded' : ''}`}
                onClick={(e) => e.stopPropagation()}
                role="dialog"
                aria-modal={embedded ? undefined : 'true'}
                aria-label="Video player"
            >
                <button className="player-close" onClick={onClose} aria-label="Close">×</button>
                {videoId && (
                    <div className="player-video-id" title={String(videoId)}>
                        Video ID: {videoId}
                    </div>
                )}
                
                {/* Copy notification toast */}
                {copyMessage && (
                    <div className="copy-toast">
                        {copyMessage}
                    </div>
                )}

                <div className="player-split">
                    {/* LEFT: video */}
                    <div className="player-left">
                        <div className="player-actions">
                            <button className="add-btn" onClick={addKeyframe}>＋ Add keyframe</button>
                            {sortedMarkers.length > 0 && (
                                <>
                                    <button className="prev-btn" onClick={jumpPrevious}>Previous</button>
                                    <button className="next-btn" onClick={jumpNext}>Next</button>
                                </>
                            )}
                            {activeFps && (
                                <div className="fps-info">
                                    FPS: {activeFps} | Frame: {timestampToFrameNumber(getCurrentTime(), activeFps)}
                                </div>
                            )}
                        </div>

                        {isYouTube ? (
                            <div className="player-iframe-wrapper" key="yt">
                                <div className="yt-anchor" ref={ytDivRef} />
                            </div>
                        ) : isBunnyEmbed ? (
                            <div className="player-iframe-wrapper" key="bunny">
                                <iframe
                                    className="player-video"
                                    src={bunnySrc}
                                    title="Bunny Stream"
                                    allow="accelerometer; autoplay; encrypted-media; picture-in-picture; fullscreen"
                                    allowFullScreen
                                    style={{ border: 0, width: '100%', height: '100%' }}
                                />
                            </div>
                        ) : (
                            <div className="player-video-wrapper" key="html">
                                <video
                                    className="player-video"
                                    ref={htmlVideoRef}
                                    src={videoUrl}
                                    controls
                                    autoPlay
                                    onLoadedMetadata={(e) => {
                                        setDuration(e.currentTarget.duration || 0);
                                        e.currentTarget.currentTime = startSeconds;
                                    }}
                                />
                            </div>
                        )}

                        <button
                            type="button"
                            className="seek-side seek-side-left"
                            onClick={() => nudgeSeconds(-5)}
                            aria-label="Back 5 seconds"
                        >
                            −5s
                        </button>
                        <button
                            type="button"
                            className="seek-side seek-side-right"
                            onClick={() => nudgeSeconds(5)}
                            aria-label="Forward 5 seconds"
                        >
                            +5s
                        </button>

                        {duration > 0 && (
                            <div className="marker-bar">
                                {sortedMarkers.map((m, i) => (
                                    <span
                                        key={`mk-${i}`}
                                        className="marker-dot"
                                        style={{ left: `${(m / duration) * 100}%` }}
                                        title={`Keyframe @ ${m}s`}
                                        onClick={() => seekTo(m)}
                                    />
                                ))}
                            </div>
                        )}
                    </div>

                    {/* RIGHT: list */}
                    <aside className="player-right">
                        <div className="dres-player-box">
                            <div>
                                {playheadMs == null
                                    ? 'Thiếu fps metadata — không nộp DRES'
                                    : `${playheadMs} ms · frame ${playheadFrame}`}
                            </div>
                            <button
                                type="button"
                                className="dres-player-btn"
                                onClick={submitPlayhead}
                                disabled={playheadMs == null || dres.submitting}
                            >
                                {dres.taskType === 'trake' ? '＋ TRAKE mốc này' : 'Nộp mốc đang phát'}
                            </button>
                            {dres.taskType === 'trake' && (
                                <button
                                    type="button"
                                    className="dres-player-btn"
                                    onClick={sendPickedAsTrake}
                                    disabled={!strictFps || picked.length === 0}
                                >
                                    List pick → TRAKE
                                </button>
                            )}
                        </div>
                        <div className="marks-header">
                            <span>Keyframes ({picked.length})</span>
                            {picked.length > 0 && (
                                <button className="copy-all-btn" onClick={copyAllKeyframes} title="Copy all keyframes">
                                    📋 Copy All
                                </button>
                            )}
                        </div>
                        <div className="marks-list">
                            {picked.map((p, i) => (
                                <div
                                    key={`li-${i}`}
                                    className="mark-item"
                                    draggable
                                    onDragStart={onDragStartPicked(i)}
                                    onDragOver={(e) => e.preventDefault()}
                                    onDrop={onDropPicked(i)}
                                >
                                    <button className="mark-time" onClick={() => seekTo(p.sec)}>{p.line}</button>
                                    <button className="mark-copy" title="Copy to clipboard" onClick={() => copyKeyframe(p.line)}>📋</button>
                                    <button className="mark-dl" title="Download CSV" onClick={() => downloadOneCSV(p.line)}>⬇</button>
                                    <button className="mark-del" onClick={() => removePicked(i)}>✕</button>
                                </div>
                            ))}
                            {picked.length === 0 && (
                                <div className="marks-empty">Chưa có keyframe nào. Bấm “＋ Add keyframe”.</div>
                            )}
                        </div>
                    </aside>
                </div>
            </div>
        </div>
    );
};

export default VideoPlayerModal;
