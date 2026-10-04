import React, { useRef, useState } from 'react';
import DresShotButton from './DresShotButton';
import VideoPlayerModal from './VideoPlayModal';
import { apiUrl } from '../config';
import { submitOnEnter } from '../utils/submitOnEnter';
import { createPlayerState, EMPTY_PLAYER_STATE } from '../utils/videoPlayerUtils';

const emptyShot = {
  video_id: '',
  frame: null,
  ms: null,
  fps: null,
  clock: '',
  junction: '',
  date: '',
  daynight: '',
  start: '',
  end: '',
  inside: null,
  conf: '',
  video_url: '',
};

const CCTVTab = () => {
  const [videoId, setVideoId] = useState('N065-V001');
  const [clockText, setClockText] = useState('19:02:22');
  const [frameText, setFrameText] = useState('');
  const [street, setStreet] = useState('');
  const [dateText, setDateText] = useState('');
  const [daynight, setDaynight] = useState('');
  const [whenText, setWhenText] = useState('07/06 19:02:22');
  const [whenStreet, setWhenStreet] = useState('');
  const [cameras, setCameras] = useState([]);
  const [atRows, setAtRows] = useState([]);
  const [shot, setShot] = useState(emptyShot);
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');
  const [hiresUrl, setHiresUrl] = useState('');
  const [hiresNote, setHiresNote] = useState('');
  const [player, setPlayer] = useState(EMPTY_PLAYER_STATE);
  const [inlinePlayer, setInlinePlayer] = useState(EMPTY_PLAYER_STATE);
  const inlineRef = useRef(null);

  const readError = async (response) => {
    try {
      const data = await response.json();
      return data.detail || data.description || `HTTP ${response.status}`;
    } catch {
      return `HTTP ${response.status}`;
    }
  };

  const run = async (label, job) => {
    setError('');
    setBusy(label);
    try {
      await job();
    } catch (err) {
      setError(String(err?.message || err));
    } finally {
      setBusy('');
    }
  };

  const applyShot = (data) => {
    setShot({
      video_id: data.video_id || '',
      frame: data.frame ?? null,
      ms: data.ms ?? null,
      fps: data.fps ?? null,
      clock: data.clock || '',
      junction: data.junction || '',
      date: data.date || '',
      daynight: data.daynight || '',
      start: data.start || '',
      end: data.end || '',
      inside: data.inside ?? null,
      conf: data.conf || '',
      video_url: data.video_url || '',
    });
    if (data.video_id) setVideoId(data.video_id);
    if (data.frame != null) setFrameText(String(data.frame));
    if (data.clock) setClockText(data.clock.split(' ').pop());
    setHiresUrl('');
    setHiresNote('');
  };

  const convertClock = () => run('clock', async () => {
    const params = new URLSearchParams({ video: videoId.trim(), time: clockText.trim() });
    const response = await fetch(apiUrl(`/cctv/clock?${params}`));
    if (!response.ok) throw new Error(await readError(response));
    applyShot(await response.json());
  });

  const convertFrame = () => run('frame', async () => {
    const params = new URLSearchParams({ video: videoId.trim(), frame: frameText.trim() });
    const response = await fetch(apiUrl(`/cctv/frame?${params}`));
    if (!response.ok) throw new Error(await readError(response));
    applyShot(await response.json());
  });

  const findCameras = () => run('find', async () => {
    const params = new URLSearchParams();
    if (street.trim()) params.set('q', street.trim());
    if (dateText.trim()) params.set('date', dateText.trim());
    if (daynight) params.set('daynight', daynight);
    const response = await fetch(apiUrl(`/cctv/find?${params}`));
    if (!response.ok) throw new Error(await readError(response));
    const data = await response.json();
    setCameras(data.results || []);
  });

  const listAt = () => run('at', async () => {
    const params = new URLSearchParams({ when: whenText.trim() });
    if (whenStreet.trim()) params.set('q', whenStreet.trim());
    const response = await fetch(apiUrl(`/cctv/at?${params}`));
    if (!response.ok) throw new Error(await readError(response));
    const data = await response.json();
    setAtRows(data.results || []);
  });

  const showBelow = (row, startSeconds = 0) => {
    if (!row?.video_url) {
      setError('Chưa có link video cho camera này.');
      setInlinePlayer(EMPTY_PLAYER_STATE);
      return;
    }
    const fps = Number(row.fps) || null;
    const frame = row.frame != null ? Number(row.frame) : 0;
    setInlinePlayer(createPlayerState({
      videoUrl: row.video_url,
      startSeconds,
      keyframeRefs: fps ? [{
        video_id: row.video_id,
        keyframe_num: frame,
        fps,
        timestamp: startSeconds,
      }] : [],
      videoId: row.video_id,
      frameRate: fps,
    }));
    window.setTimeout(() => {
      inlineRef.current?.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    }, 0);
  };

  const chooseCamera = (cam) => {
    setVideoId(cam.video_id);
    setShot({ ...emptyShot, ...cam });
    showBelow(cam, 0);
  };

  const openWatch = (row) => {
    if (!row?.video_url || row.frame == null || row.fps == null) {
      setError('Chưa có link video cho khung này.');
      return;
    }
    const startSeconds = Number(row.frame) / Number(row.fps);
    setPlayer(createPlayerState({
      videoUrl: row.video_url,
      startSeconds,
      keyframeRefs: [{
        video_id: row.video_id,
        keyframe_num: row.frame,
        fps: row.fps,
        timestamp: startSeconds,
      }],
      videoId: row.video_id,
      frameRate: row.fps,
    }));
  };

  const grabHires = (video, frame) => run('hires', async () => {
    const vid = (video || videoId).trim();
    const fr = frame != null ? frame : frameText.trim();
    if (!vid || fr === '' || fr == null) throw new Error('Cần video và số khung');
    const url = apiUrl(`/cctv/hires/${encodeURIComponent(vid)}/${fr}.jpg`);
    const response = await fetch(url);
    if (!response.ok) throw new Error(await readError(response));
    const blob = await response.blob();
    setHiresUrl((prev) => {
      if (prev.startsWith('blob:')) URL.revokeObjectURL(prev);
      return URL.createObjectURL(blob);
    });
    setHiresNote(`${vid} · khung ${fr} · 1080p`);
    setVideoId(vid);
    setFrameText(String(fr));
  });

  return (
    <div className="cctv-tab">
      <h2>CCTV — nhóm N</h2>
      <p className="description">
        298 camera giao thông. Giờ lấy từ đồng hồ in trên hình. Đổi giờ ra khung và millisecond để nộp, hoặc cắt ảnh 1080p để đọc biển số.
      </p>

      {error && <div className="error-message">Error: {error}</div>}
      {busy && <div className="loading-message">{busy === 'hires' ? 'Đang cắt ảnh 1080p…' : 'Đang tính…'}</div>}

      <div className="cctv-grid">
        <section className="cctv-card">
          <h3>Đồng hồ → khung</h3>
          <label>
            Video
            <input value={videoId} onChange={(e) => setVideoId(e.target.value)} onKeyDown={(e) => submitOnEnter(e, convertClock)} placeholder="N065-V001" />
          </label>
          <label>
            Giờ trên hình
            <input value={clockText} onChange={(e) => setClockText(e.target.value)} onKeyDown={(e) => submitOnEnter(e, convertClock)} placeholder="19:02:22 hoặc 07/06 19:02:22" />
          </label>
          <div className="cctv-actions">
            <button type="button" onClick={convertClock} disabled={!!busy}>Đổi ra khung</button>
            <button type="button" className="secondary" onClick={() => grabHires(shot.video_id, shot.frame)} disabled={!!busy || shot.frame == null}>
              Ảnh 1080p
            </button>
          </div>
          <label>
            Khung → giờ
            <input value={frameText} onChange={(e) => setFrameText(e.target.value)} onKeyDown={(e) => submitOnEnter(e, convertFrame)} placeholder="3748" />
          </label>
          <button type="button" className="secondary" onClick={convertFrame} disabled={!!busy}>Đổi ra giờ</button>
        </section>

        <section className="cctv-card">
          <h3>Khung đang chọn</h3>
          {shot.video_id ? (
            <>
              <p className="cctv-shot-line">
                <strong>{shot.video_id}</strong>
                {shot.junction ? ` · ${shot.junction}` : ''}
              </p>
              <p className="cctv-shot-line">
                {shot.date} {shot.start && shot.end ? `${shot.start}–${shot.end}` : ''} {shot.daynight}
                {shot.conf ? ` · ${shot.conf}` : ''}
              </p>
              <p className="cctv-shot-ms">
                {shot.clock || '—'} → khung{' '}
                {shot.frame != null ? (
                  <button type="button" className="cctv-frame-link" onClick={() => openWatch(shot)}>
                    {shot.frame}
                  </button>
                ) : '—'}
                {' '}· <strong>{shot.ms ?? '—'} ms</strong>
              </p>
              {shot.inside === false && <p className="cctv-warn">Mốc này nằm ngoài thời lượng video.</p>}
              {shot.fps != null && shot.frame != null && (
                <DresShotButton videoId={shot.video_id} frame={shot.frame} fps={shot.fps} source="cctv" />
              )}
            </>
          ) : (
            <p className="description">Chưa chọn mốc. Ví dụ đã soát: N065-V001 lúc 19:02:22 là khung 3748.</p>
          )}
          {hiresUrl && (
            <figure className="cctv-hires">
              <button type="button" className="cctv-hires-btn" onClick={() => openWatch(shot)} title="Xem video tại khung này">
                <img src={hiresUrl} alt={hiresNote || 'Khung 1080p'} />
              </button>
              <figcaption>{hiresNote} · bấm ảnh để xem video</figcaption>
            </figure>
          )}
        </section>
      </div>

      <section className="cctv-card">
        <h3>Tìm camera</h3>
        <div className="cctv-filters">
          <label>
            Đường / ngã tư
            <input value={street} onChange={(e) => setStreet(e.target.value)} onKeyDown={(e) => submitOnEnter(e, findCameras)} placeholder="Tôn Thất Tùng" />
          </label>
          <label>
            Ngày
            <input value={dateText} onChange={(e) => setDateText(e.target.value)} onKeyDown={(e) => submitOnEnter(e, findCameras)} placeholder="07/06" />
          </label>
          <label>
            Ngày / đêm
            <select value={daynight} onChange={(e) => setDaynight(e.target.value)}>
              <option value="">Tất cả</option>
              <option value="day">Ban ngày</option>
              <option value="night">Ban đêm</option>
            </select>
          </label>
          <button type="button" onClick={findCameras} disabled={!!busy}>Tìm</button>
        </div>
        {cameras.length > 0 && (
          <table className="cctv-table">
            <thead>
              <tr>
                <th>Video</th>
                <th>Ngã tư</th>
                <th>Ngày</th>
                <th>Đồng hồ</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {cameras.map((cam) => (
                <tr key={cam.video_id}>
                  <td>{cam.video_id}</td>
                  <td>{cam.junction}</td>
                  <td>{cam.date} · {cam.daynight === 'night' ? 'đêm' : 'ngày'}</td>
                  <td>{cam.start}–{cam.end}</td>
                  <td>
                    <button type="button" className="secondary" onClick={() => chooseCamera(cam)}>
                      Chọn
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        {inlinePlayer.open && (
          <div className="cctv-inline-player" ref={inlineRef}>
            <VideoPlayerModal
              embedded
              videoUrl={inlinePlayer.url}
              startSeconds={inlinePlayer.t}
              markers={inlinePlayer.markers}
              keyframeRefs={inlinePlayer.keyframeRefs}
              onClose={() => setInlinePlayer(EMPTY_PLAYER_STATE)}
              videoId={inlinePlayer.videoId}
              csvBaseName={inlinePlayer.csvBaseName}
              frameRate={inlinePlayer.frameRate}
            />
          </div>
        )}
      </section>

      <section className="cctv-card">
        <h3>Camera đang quay lúc</h3>
        <div className="cctv-filters">
          <label>
            Thời điểm
            <input value={whenText} onChange={(e) => setWhenText(e.target.value)} onKeyDown={(e) => submitOnEnter(e, listAt)} placeholder="07/06 19:02:22" />
          </label>
          <label>
            Lọc đường
            <input value={whenStreet} onChange={(e) => setWhenStreet(e.target.value)} onKeyDown={(e) => submitOnEnter(e, listAt)} placeholder="tuỳ chọn" />
          </label>
          <button type="button" onClick={listAt} disabled={!!busy}>Liệt kê</button>
        </div>
        {atRows.length > 0 && (
          <table className="cctv-table">
            <thead>
              <tr>
                <th>Video</th>
                <th>Ngã tư</th>
                <th>Khung</th>
                <th>ms</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {atRows.map((row) => (
                <tr key={row.video_id}>
                  <td>{row.video_id}</td>
                  <td>{row.junction}<div className="cctv-sub">{row.date} {row.daynight === 'night' ? 'đêm' : 'ngày'}</div></td>
                  <td>
                    <button type="button" className="cctv-frame-link" onClick={() => openWatch(row)}>
                      {row.frame}
                    </button>
                  </td>
                  <td>{row.ms}</td>
                  <td className="cctv-row-actions">
                    <button type="button" className="secondary" onClick={() => applyShot(row)}>Chọn</button>
                    <button type="button" className="secondary" onClick={() => grabHires(row.video_id, row.frame)} disabled={!!busy}>1080p</button>
                    {row.fps != null && (
                      <DresShotButton videoId={row.video_id} frame={row.frame} fps={row.fps} source="cctv" />
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
      {player.open && (
        <VideoPlayerModal
          videoUrl={player.url}
          startSeconds={player.t}
          markers={player.markers}
          keyframeRefs={player.keyframeRefs}
          onClose={() => setPlayer(EMPTY_PLAYER_STATE)}
          videoId={player.videoId}
          csvBaseName={player.csvBaseName}
          frameRate={player.frameRate}
        />
      )}
    </div>
  );
};

export default CCTVTab;
