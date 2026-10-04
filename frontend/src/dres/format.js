import { frameToMs, resolveFps } from '../utils/fpsUtils';

export const TASKS = [
  { id: 'textual-kis', label: 'Textual KIS', windowSec: 5 * 60 },
  { id: 'video-kis', label: 'Video KIS', windowSec: 4 * 60 },
  { id: 'qa', label: 'Q&A', windowSec: 5 * 60 },
  { id: 'trake', label: 'TRAKE', windowSec: 5 * 60 },
];

export const taskWindowSec = (taskType) =>
  TASKS.find((task) => task.id === taskType)?.windowSec ?? 5 * 60;

export const mediaItemName = (videoId) => {
  const raw = String(videoId || '').trim();
  if (!raw) return '';
  const base = raw.split('/').pop().split('\\').pop();
  return base.replace(/\.(mp4|mkv|webm|mov|avi)$/i, '');
};

export const buildShot = ({ videoId, frame, fps, rank = null, source = 'result' } = {}) => {
  const rate = resolveFps(fps);
  const frameNum = Number(frame);
  const validFrame = Number.isFinite(frameNum) ? frameNum : null;
  return {
    videoId: mediaItemName(videoId),
    frame: validFrame,
    fps: rate,
    ms: frameToMs(validFrame, rate),
    rank: Number.isFinite(rank) ? rank : null,
    source,
  };
};

const textBody = (text) => ({
  answerSets: [{ answers: [{ text }] }],
});

const kisBody = (name, start, end) => ({
  answerSets: [{
    answers: [{
      mediaItemName: name,
      start,
      end,
    }],
  }],
});

export const buildAnswer = ({
  taskType,
  shot = null,
  shots = [],
  qaAnswer = '',
  endPadMs = 0,
} = {}) => {
  const pad = Number.isFinite(Number(endPadMs)) ? Math.max(0, Math.round(Number(endPadMs))) : 0;

  if (taskType === 'textual-kis' || taskType === 'video-kis') {
    if (!shot?.videoId) return { ok: false, error: 'Chưa chọn video.' };
    if (shot.ms == null || !shot.fps) {
      return { ok: false, error: 'Thiếu fps metadata — không quy đổi ms và không đoán fps.' };
    }
    const start = shot.ms;
    const end = start + pad;
    const body = kisBody(shot.videoId, start, end);
    const preview = `${shot.videoId}  ${start}–${end} ms`;
    return { ok: true, body, preview, fingerprint: `KIS|${shot.videoId}|${start}|${end}` };
  }

  if (taskType === 'qa') {
    const answer = String(qaAnswer || '').trim();
    if (!answer) return { ok: false, error: 'Nhập đáp án Q&A trước khi nộp.' };
    if (answer.includes('-')) {
      return { ok: false, error: 'Đáp án Q&A không được chứa dấu "-".' };
    }
    if (!shot?.videoId) return { ok: false, error: 'Chưa chọn video.' };
    if (shot.ms == null || !shot.fps) {
      return { ok: false, error: 'Thiếu fps metadata — không quy đổi ms và không đoán fps.' };
    }
    const text = `QA-${answer}-${shot.videoId}-${shot.ms}`;
    return { ok: true, body: textBody(text), preview: text, fingerprint: text };
  }

  if (taskType === 'trake') {
    const list = Array.isArray(shots) ? shots : [];
    if (!list.length) return { ok: false, error: 'TRAKE chưa có frame. Bấm ＋ TRAKE theo đúng thứ tự.' };
    const videoId = list[0]?.videoId;
    if (!videoId) return { ok: false, error: 'TRAKE thiếu video id.' };
    if (list.some((item) => item?.videoId !== videoId)) {
      return { ok: false, error: 'TRAKE chỉ nộp một video. Xóa frame video khác.' };
    }
    if (list.some((item) => item?.ms == null || !item?.fps || !Number.isFinite(item?.frame))) {
      return { ok: false, error: 'Một frame TRAKE thiếu fps metadata.' };
    }
    const frames = list.map((item) => String(Math.trunc(item.frame)));
    const text = `TR-${videoId}-${frames.join(',')}`;
    return { ok: true, body: textBody(text), preview: text, fingerprint: text };
  }

  return { ok: false, error: 'Chưa chọn loại câu.' };
};

export const interpretVerdict = (data) => {
  const submission = String(data?.submission || '').toUpperCase();
  const description = String(data?.description || '').toLowerCase();
  if (
    submission === 'WRONG'
    || description.includes('wrong')
    || description.includes('incorrect')
  ) {
    return 'WRONG';
  }
  if (submission === 'INDETERMINATE') return 'INDETERMINATE';
  if (submission === 'UNDECIDABLE') return 'UNDECIDABLE';
  const partial = description.includes('partial') || description.includes('một phần');
  if (submission === 'CORRECT' || description.includes('correct')) {
    return partial ? 'PARTIAL' : 'CORRECT';
  }
  return 'SUBMITTED';
};

export const SCORE = { max: 100, base: 50, penalty: 10 };

export const estimateScore = ({ elapsedSec = 0, taskType, wrongCount = 0, partial = false } = {}) => {
  const T = taskWindowSec(taskType);
  const t = Math.min(Math.max(0, Number(elapsedSec) || 0), T);
  const timeFactor = 1 - t / T;
  const full = Math.max(
    0,
    SCORE.base + (SCORE.max - SCORE.base) * timeFactor - wrongCount * SCORE.penalty,
  );
  return {
    T,
    t,
    remain: Math.max(0, T - t),
    full,
    score: partial ? full / 2 : full,
    secondsPerPoint: T / (SCORE.max - SCORE.base),
  };
};

export const formatClock = (sec) => {
  const total = Math.max(0, Math.ceil(Number(sec) || 0));
  const minutes = Math.floor(total / 60);
  const seconds = total % 60;
  return `${minutes}:${String(seconds).padStart(2, '0')}`;
};

export const scoreHint = (estimate, { timerOn, hasTarget, wrongCount, earlyRank }) => {
  if (!timerOn) {
    return 'Bấm “Câu mới” khi DRES mở đề để đếm 4′ / 5′.';
  }
  const points = estimate.score.toFixed(1);
  if (earlyRank != null && earlyRank <= 20) {
    return `Top ${earlyRank}: chọn frame giữa đoạn (tránh mép), nộp nếu đã chắc (~${points}, k=${wrongCount}).`;
  }
  if (!hasTarget) {
    return `Chưa chọn mốc. Nếu đúng lúc này ≈ ${points} (k=${wrongCount}).`;
  }
  if (estimate.remain <= 60) {
    return `Còn ${formatClock(estimate.remain)} — nên nộp nếu đã chắc (~${points} điểm).`;
  }
  if (estimate.score >= 85) {
    return `Điểm còn cao (~${points}). Đã chắc thì nộp sớm; mỗi lần sai trừ ${SCORE.penalty}.`;
  }
  return `Nếu đúng lúc này ≈ ${points} điểm (k=${wrongCount}).`;
};
