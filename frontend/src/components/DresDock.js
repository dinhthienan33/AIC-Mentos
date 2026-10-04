import React, { useEffect, useState } from 'react';
import { useDres } from '../dres/DresContext';
import { TASKS, estimateScore, formatClock, scoreHint } from '../dres/format';

const CHECKLIST = [
  'Watcher đọc đề trên DRES (👁). Video KIS: không chụp clip.',
  'Operator dán mô tả tiếng Việt vào ô search.',
  'Giữ SigLIP2 + Translate ON.',
  'TRAKE hoặc nhiều sự kiện: Temporal ON.',
  'Hit trong top 20: chọn frame giữa đoạn, rồi nộp.',
];

const DresDock = () => {
  const dres = useDres();
  const [open, setOpen] = useState(false);
  const [now, setNow] = useState(Date.now());
  const [connecting, setConnecting] = useState(false);
  const [scene, setScene] = useState('');
  const [objects, setObjects] = useState('');
  const [action, setAction] = useState('');
  const [ocrText, setOcrText] = useState('');

  useEffect(() => {
    if (!dres.drawerSignal) return;
    setOpen(true);
  }, [dres.drawerSignal]);

  useEffect(() => {
    setScene('');
    setObjects('');
    setAction('');
    setOcrText('');
  }, [dres.questionNonce]);

  useEffect(() => {
    if (!dres.startedAt) return undefined;
    const id = setInterval(() => setNow(Date.now()), 250);
    return () => clearInterval(id);
  }, [dres.startedAt]);

  const elapsedSec = dres.startedAt ? (now - dres.startedAt) / 1000 : 0;
  const live = estimateScore({
    elapsedSec,
    taskType: dres.taskType,
    wrongCount: dres.wrongCount,
    partial: false,
  });
  const estimate = dres.lockedScore
    ? estimateScore({
      elapsedSec: dres.lockedScore.elapsedSec,
      taskType: dres.taskType,
      wrongCount: dres.lockedScore.wrongCount,
      partial: Boolean(dres.lockedScore.partial),
    })
    : live;
  const earlyRank = dres.taskType === 'trake' ? null : dres.lastShot?.rank;
  const hint = dres.lockedScore
    ? `Đã chốt ${estimate.score.toFixed(1)} (${dres.lockedScore.verdict}).`
    : scoreHint(live, {
      timerOn: Boolean(dres.startedAt),
      hasTarget: Boolean(dres.draft?.ok),
      wrongCount: dres.wrongCount,
      earlyRank,
    });
  const task = TASKS.find((item) => item.id === dres.taskType) || TASKS[0];
  const remain = dres.startedAt ? live.remain : task.windowSec;
  const showChecklist = !dres.startedAt || elapsedSec < 30;

  const onConnect = async () => {
    setConnecting(true);
    try {
      const liveSession = await dres.connect({ forceLogin: true });
      const count = liveSession.evaluations?.length || 1;
      const name = liveSession.evaluation.name || liveSession.evaluation.id;
      dres.setStatus({ level: 'ok', text: `${count} evaluation ACTIVE. Đang nộp vào ${name}.` });
    } catch (err) {
      dres.setStatus({ level: 'err', text: err.message || 'Không nối được DRES.' });
    } finally {
      setConnecting(false);
    }
  };

  const applyDescription = () => {
    const lines = [
      scene.trim() && `Bối cảnh: ${scene.trim()}`,
      objects.trim() && `Vật nổi / màu / logo: ${objects.trim()}`,
      action.trim() && `Hành động: ${action.trim()}`,
      ocrText.trim() && `Chữ OCR: ${ocrText.trim()}`,
    ].filter(Boolean);
    if (!lines.length) return;
    dres.applyQuery(lines.join('\n'));
  };

  return (
    <div className="dres-anchor">
      <div className="dres-strip" role="region" aria-label="DRES submit">
        <label className="dres-task">
          <span className="dres-kicker">Eval</span>
          <select
            className="dres-eval-select"
            value={dres.evaluation?.id || ''}
            onChange={(event) => dres.selectEvaluation(event.target.value)}
            disabled={!dres.evaluations.length}
            title={dres.evaluation ? `${dres.evaluation.name} · ${dres.evaluation.id}` : 'Login để lấy các evaluation ACTIVE'}
          >
            {!dres.evaluations.length && <option value="">Chưa nối</option>}
            {dres.evaluations.map((item) => (
              <option key={item.id} value={item.id}>{item.name}</option>
            ))}
          </select>
        </label>
        <label className="dres-task">
          <span className="dres-kicker">DRES</span>
          <select
            value={dres.taskType}
            onChange={(event) => dres.setTaskType(event.target.value)}
          >
            {TASKS.map((item) => (
              <option key={item.id} value={item.id}>{item.label}</option>
            ))}
          </select>
        </label>
        <div className="dres-metric" title={dres.evaluation ? dres.evaluation.name : 'Chưa nối evaluation'}>
          <span className="dres-metric-label">{dres.startedAt ? 'còn' : 'cửa sổ'}</span>
          <strong>{formatClock(remain)}</strong>
        </div>
        <div className="dres-metric">
          <span className="dres-metric-label">{dres.lockedScore ? 'đã chốt' : 'nếu đúng'}</span>
          <strong>{estimate.score.toFixed(1)}</strong>
        </div>
        <div className="dres-metric">
          <span className="dres-metric-label">k sai</span>
          <strong>{dres.wrongCount}</strong>
        </div>
        <div className={`dres-draft${dres.draft?.ok ? '' : ' is-empty'}`} title={dres.draft?.preview || dres.draft?.error || ''}>
          {dres.draft?.ok ? dres.draft.preview : (dres.draft?.error || 'Chưa chọn mốc')}
        </div>
        <button type="button" className="dres-btn dres-btn-primary" disabled={dres.submitting} onClick={dres.startQuestion}>
          Câu mới
        </button>
        <button
          type="button"
          className="dres-btn dres-btn-submit"
          disabled={dres.submitting || !dres.draft?.ok}
          onClick={() => dres.submitCurrent({ dryRun: false, force: false })}
          title="Nộp đáp án đang chọn (Alt+S)"
        >
          {dres.submitting ? 'Đang nộp…' : 'Nộp DRES'}
        </button>
        <button type="button" className="dres-btn" onClick={() => setOpen((value) => !value)}>
          {open ? 'Đóng' : 'Chi tiết'}
        </button>
      </div>
      <p className={`dres-hint${estimate.remain <= 60 && dres.startedAt ? ' is-urgent' : ''}`}>{hint}</p>
      {dres.status && (
        <p className={`dres-status is-${dres.status.level}`}>{dres.status.text}</p>
      )}

      {open && (
        <div className="dres-drawer">
          <div className="dres-drawer-grid">
            <label>
              Server
              <input
                value={dres.baseUrl}
                onChange={(event) => dres.setBaseUrl(event.target.value)}
                placeholder="https://eventretrieval.one"
              />
            </label>
            <label>
              Username
              <input
                value={dres.username}
                onChange={(event) => dres.setUsername(event.target.value)}
                autoComplete="username"
              />
            </label>
            <label>
              Password
              <input
                type="password"
                value={dres.password}
                onChange={(event) => dres.setPassword(event.target.value)}
                autoComplete="current-password"
              />
            </label>
          </div>
          <div className="dres-row">
            <button type="button" className="dres-btn dres-btn-primary" disabled={connecting} onClick={onConnect}>
              {connecting ? 'Đang nối…' : 'Login, lấy evaluation ACTIVE'}
            </button>
            <span className="dres-eval">
              {dres.evaluation
                ? `${dres.evaluation.name} · ${dres.evaluation.id}`
                : 'session lấy lúc nộp, không hard-code'}
            </span>
          </div>

          {showChecklist && (
            <ol className="dres-check">
              {CHECKLIST.map((item) => <li key={item}>{item}</li>)}
            </ol>
          )}

          {dres.taskType === 'video-kis' && (
            <div className="dres-template">
              <div className="dres-template-title">Mô tả nhanh (không chụp clip)</div>
              <input placeholder="Bối cảnh" value={scene} onChange={(event) => setScene(event.target.value)} />
              <input placeholder="Vật nổi / màu / logo" value={objects} onChange={(event) => setObjects(event.target.value)} />
              <input placeholder="Hành động" value={action} onChange={(event) => setAction(event.target.value)} />
              <input placeholder="Chữ OCR" value={ocrText} onChange={(event) => setOcrText(event.target.value)} />
              <button type="button" className="dres-btn" onClick={applyDescription}>Đưa vào ô search</button>
            </div>
          )}

          {dres.taskType === 'qa' && (
            <label className="dres-block">
              Đáp án Q&A (không dấu -)
              <input
                value={dres.qaAnswer}
                onChange={(event) => dres.setQaAnswer(event.target.value)}
                placeholder="ví dụ RED hoặc 42"
              />
            </label>
          )}

          {(dres.taskType === 'textual-kis' || dres.taskType === 'video-kis') && (
            <label className="dres-block">
              Kéo dài end (ms). 0 = start và end cùng mốc frame.
              <input
                type="number"
                min="0"
                step="1"
                value={dres.endPadMs}
                onChange={(event) => dres.setEndPadMs(event.target.value === '' ? 0 : Number(event.target.value))}
              />
            </label>
          )}

          {dres.taskType === 'trake' && (
            <div className="dres-trake">
              <div className="dres-template-title">Thứ tự frame TRAKE</div>
              {dres.trakeShots.length === 0 && <p className="dres-muted">Bấm ＋ TRAKE trên từng frame, đúng thứ tự sự kiện.</p>}
              {dres.trakeShots.map((shot, index) => (
                <div className="dres-trake-row" key={`${shot.videoId}-${shot.frame}-${index}`}>
                  <span>{index + 1}. {shot.videoId} · frame {shot.frame}</span>
                  <span>
                    <button type="button" className="dres-mini" onClick={() => dres.moveTrake(index, -1)}>↑</button>
                    <button type="button" className="dres-mini" onClick={() => dres.moveTrake(index, 1)}>↓</button>
                    <button type="button" className="dres-mini" onClick={() => dres.removeTrakeAt(index)}>✕</button>
                  </span>
                </div>
              ))}
            </div>
          )}

          <p className="dres-muted">
            ~{estimate.secondsPerPoint.toFixed(1)}s trừ 1 điểm thời gian.
            Alt+S nộp mốc đang chọn. KIS/Q&A đúng hẳn mới có điểm; TRAKE partial còn một nửa.
          </p>

          <div className="dres-row">
            <button type="button" className="dres-btn" onClick={() => dres.widenResults(100)}>Results 100</button>
            <button type="button" className="dres-btn" onClick={() => dres.widenResults(1000)}>Results 1000</button>
            <button type="button" className="dres-btn" onClick={() => dres.enableFilter('ocr')}>Bật OCR</button>
            <button type="button" className="dres-btn" onClick={() => dres.enableFilter('od')}>Bật OD</button>
            <button type="button" className="dres-btn" disabled={dres.kFrozen} onClick={dres.markWrong}>+1 lần sai</button>
          </div>

          <div className="dres-row">
            <button
              type="button"
              className="dres-btn"
              disabled={dres.submitting}
              onClick={() => dres.submitCurrent({ dryRun: true, force: false })}
            >
              Dry-run payload
            </button>
            {dres.pendingDuplicate && (
              <button type="button" className="dres-btn dres-btn-warn" onClick={dres.confirmDuplicate}>
                Vẫn nộp trùng
              </button>
            )}
          </div>

          {dres.preview?.body && (
            <pre className="dres-payload">{JSON.stringify(dres.preview.body, null, 2)}</pre>
          )}
        </div>
      )}
    </div>
  );
};

export default DresDock;
