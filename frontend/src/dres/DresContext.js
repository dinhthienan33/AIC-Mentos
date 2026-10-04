import React, {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react';
import {
  dresListEvaluations,
  dresLogin,
  dresSubmit,
  errorText,
  activeEvaluations,
  taskTypeFromEvaluationName,
} from './client';
import { TASKS, buildAnswer, buildShot, interpretVerdict } from './format';

const DresContext = createContext(null);

const BASE_KEY = 'mentos.dres.baseUrl';
const USER_KEY = 'mentos.dres.username';
const PASS_KEY = 'mentos.dres.password';
const SESSION_KEY = 'mentos.dres.sessionId';
const TASK_KEY = 'mentos.dres.taskType';
const EVAL_KEY = 'mentos.dres.evaluationId';

const readStorage = (store, key, fallback) => {
  try {
    return store.getItem(key) || fallback;
  } catch {
    return fallback;
  }
};

export const DresProvider = ({ children }) => {
  const searchApi = useRef({});
  const [baseUrl, setBaseUrl] = useState(() =>
    readStorage(localStorage, BASE_KEY, process.env.REACT_APP_DRES_URL || ''));
  const [username, setUsername] = useState(() => readStorage(localStorage, USER_KEY, ''));
  const [password, setPassword] = useState(() => readStorage(sessionStorage, PASS_KEY, ''));
  const [sessionId, setSessionId] = useState(() => readStorage(sessionStorage, SESSION_KEY, ''));
  const [taskType, setTaskTypeState] = useState(() => {
    const stored = readStorage(sessionStorage, TASK_KEY, 'textual-kis');
    return TASKS.some((task) => task.id === stored) ? stored : 'textual-kis';
  });
  const [qaAnswer, setQaAnswer] = useState('');
  const [endPadMs, setEndPadMs] = useState(0);
  const [evaluation, setEvaluation] = useState(null);
  const [evaluations, setEvaluations] = useState([]);
  const [evaluationId, setEvaluationId] = useState(() => readStorage(sessionStorage, EVAL_KEY, ''));
  const [lastShot, setLastShot] = useState(null);
  const [trakeShots, setTrakeShots] = useState([]);
  const [wrongCount, setWrongCount] = useState(0);
  const [kFrozen, setKFrozen] = useState(false);
  const [lockedScore, setLockedScore] = useState(null);
  const [startedAt, setStartedAt] = useState(null);
  const [submitting, setSubmitting] = useState(false);
  const [status, setStatus] = useState(null);
  const [preview, setPreview] = useState(null);
  const [pendingDuplicate, setPendingDuplicate] = useState(null);
  const [submittedKeys, setSubmittedKeys] = useState([]);
  const [drawerSignal, setDrawerSignal] = useState(0);
  const [questionNonce, setQuestionNonce] = useState(0);

  const submittedRef = useRef(new Set());
  const stateRef = useRef({});
  stateRef.current = {
    baseUrl: baseUrl.trim(),
    username: username.trim(),
    password,
    sessionId,
    evaluationId,
    evaluations,
    taskType,
    qaAnswer,
    endPadMs,
    lastShot,
    trakeShots,
    wrongCount,
    kFrozen,
    startedAt,
    submitting,
  };

  const askOpen = useCallback(() => setDrawerSignal((n) => n + 1), []);

  const registerSearchApi = useCallback((api) => {
    searchApi.current = api || {};
  }, []);

  const setTaskType = useCallback((next) => {
    setTaskTypeState(next);
    try {
      sessionStorage.setItem(TASK_KEY, next);
    } catch {
      /* ignore */
    }
    if (next === 'trake') {
      searchApi.current.setModelName?.('siglip2');
      searchApi.current.setTranslate?.(true);
      searchApi.current.setTemporalSearch?.(true);
      searchApi.current.setHybridSearch?.(false);
    }
  }, []);

  const applyQuery = useCallback((text) => {
    searchApi.current.setQuery?.(text);
    searchApi.current.ensureQueryName?.();
    searchApi.current.setModelName?.('siglip2');
    searchApi.current.setTranslate?.(true);
  }, []);

  const widenResults = useCallback((k) => {
    searchApi.current.setK?.(k);
    setStatus({ level: 'ok', text: `Results = ${k}. Search lại (Ctrl+Enter).` });
  }, []);

  const enableFilter = useCallback((kind) => {
    searchApi.current.setFilteringEnabled?.(true);
    if (kind === 'ocr') searchApi.current.setFilterOcr?.(true);
    if (kind === 'od') searchApi.current.setFilterOd?.(true);
    setStatus({ level: 'ok', text: `Đã bật filter ${kind.toUpperCase()}. Điền giá trị rồi Apply.` });
    askOpen();
  }, [askOpen]);

  const persistSession = (sid) => {
    setSessionId(sid || '');
    try {
      if (sid) sessionStorage.setItem(SESSION_KEY, sid);
      else sessionStorage.removeItem(SESSION_KEY);
    } catch {
      /* ignore */
    }
  };

  const connect = useCallback(async ({ forceLogin = false } = {}) => {
    const snap = stateRef.current;
    const root = (snap.baseUrl || '').trim().replace(/\/$/, '');
    if (!root.startsWith('https://')) {
      throw new Error('URL DRES phải bắt đầu bằng https://');
    }
    if (!snap.username || !snap.password) {
      throw new Error('Nhập username và password DRES.');
    }
    try {
      localStorage.setItem(BASE_KEY, root);
      localStorage.setItem(USER_KEY, snap.username);
      sessionStorage.setItem(PASS_KEY, snap.password);
    } catch {
      /* ignore */
    }

    let sid = forceLogin ? '' : snap.sessionId;
    if (!sid) {
      sid = await dresLogin(root, snap.username, snap.password);
      persistSession(sid);
    }

    let listed = await dresListEvaluations(root, sid);
    if (listed.status === 401) {
      sid = await dresLogin(root, snap.username, snap.password);
      persistSession(sid);
      listed = await dresListEvaluations(root, sid);
    }
    if (listed.status < 200 || listed.status >= 300) {
      throw new Error(errorText(listed.status, listed.data));
    }
    const active = activeEvaluations(listed.data).map((item) => ({
      id: String(item.id),
      name: item.name || String(item.id),
      status: item.status || 'ACTIVE',
    }));
    setEvaluations(active);
    if (!active.length) {
      setEvaluation(null);
      setEvaluationId('');
      throw new Error('Không có evaluation ACTIVE. Đợi BTC mở phiên.');
    }
    const preferred = snap.evaluationId;
    const chosen = active.find((item) => item.id === String(preferred));
    if (preferred && !chosen) {
      setEvaluation(null);
      setEvaluationId('');
      try {
        sessionStorage.removeItem(EVAL_KEY);
      } catch {
        /* ignore */
      }
      throw new Error('Evaluation đã chọn không còn ACTIVE. Chọn lại trong dropdown.');
    }
    const pick = chosen || active[0];
    setEvaluation(pick);
    setEvaluationId(pick.id);
    try {
      sessionStorage.setItem(EVAL_KEY, pick.id);
    } catch {
      /* ignore */
    }
    if (!preferred) {
      const guessed = taskTypeFromEvaluationName(pick.name);
      if (guessed) setTaskType(guessed);
    }
    return { sessionId: sid, evaluation: pick, evaluations: active, baseUrl: root };
  }, [setTaskType]);

  const selectEvaluation = useCallback((id) => {
    const found = stateRef.current.evaluations.find((item) => item.id === String(id));
    if (!found) return;
    setEvaluation(found);
    setEvaluationId(found.id);
    try {
      sessionStorage.setItem(EVAL_KEY, found.id);
    } catch {
      /* ignore */
    }
    const guessed = taskTypeFromEvaluationName(found.name);
    if (guessed) setTaskType(guessed);
    setStatus({
      level: 'ok',
      text: guessed
        ? `Nộp vào ${found.name}. Loại câu: ${guessed}.`
        : `Nộp vào ${found.name}.`,
    });
  }, [setTaskType]);

  const sendBuilt = useCallback(async (built, { dryRun = false, force = false } = {}) => {
    if (!built?.ok) {
      setStatus({ level: 'err', text: built?.error || 'Không tạo được đáp án.' });
      setPreview(built);
      askOpen();
      return;
    }
    setPreview(built);
    if (!dryRun && !force && submittedRef.current.has(built.fingerprint)) {
      setPendingDuplicate(built);
      setStatus({ level: 'warn', text: 'Đã nộp trùng kết quả này cho câu hiện tại.' });
      askOpen();
      return;
    }
    if (dryRun) {
      setStatus({ level: 'ok', text: 'Dry-run — chưa gửi lên DRES.' });
      askOpen();
      return;
    }

    setSubmitting(true);
    setPendingDuplicate(null);
    try {
      const live = await connect({ forceLogin: false });
      let { status, data } = await dresSubmit(
        live.baseUrl,
        live.sessionId,
        live.evaluation.id,
        built.body,
      );
      if (status === 401) {
        const again = await connect({ forceLogin: true });
        ({ status, data } = await dresSubmit(
          again.baseUrl,
          again.sessionId,
          again.evaluation.id,
          built.body,
        ));
      }
      if (status < 200 || status >= 300) {
        setStatus({ level: 'err', text: errorText(status, data) });
        askOpen();
        return;
      }

      submittedRef.current.add(built.fingerprint);
      setSubmittedKeys(Array.from(submittedRef.current));
      const verdict = interpretVerdict(data);
      const snap = stateRef.current;
      if (verdict === 'WRONG' && !snap.kFrozen) {
        setWrongCount((count) => count + 1);
      }
      if ((verdict === 'CORRECT' || verdict === 'PARTIAL') && !snap.kFrozen) {
        const elapsedSec = snap.startedAt ? (Date.now() - snap.startedAt) / 1000 : 0;
        setKFrozen(true);
        setLockedScore({
          verdict,
          elapsedSec,
          wrongCount: snap.wrongCount,
          partial: verdict === 'PARTIAL',
        });
      }
      const description = data?.description ? ` — ${data.description}` : '';
      setStatus({
        level: verdict === 'WRONG' ? 'err' : 'ok',
        text: `${verdict}${description}`,
      });
    } catch (err) {
      setStatus({ level: 'err', text: err.message || 'Nộp DRES thất bại.' });
      askOpen();
    } finally {
      setSubmitting(false);
    }
  }, [askOpen, connect]);

  const submitShot = useCallback((shot) => {
    const snap = stateRef.current;
    setLastShot(shot);
    if (shot?.ms == null) {
      setStatus({ level: 'err', text: 'Thiếu fps metadata — không nộp.' });
      askOpen();
      return;
    }
    if (snap.taskType === 'trake') {
      setTrakeShots((prev) => [...prev, shot]);
      setStatus({ level: 'ok', text: `TRAKE + frame ${shot.frame} (${shot.ms} ms). Nộp khi đủ thứ tự.` });
      return;
    }
    const built = buildAnswer({
      taskType: snap.taskType,
      shot,
      qaAnswer: snap.qaAnswer,
      endPadMs: snap.endPadMs,
    });
    return sendBuilt(built, { dryRun: false, force: false });
  }, [askOpen, sendBuilt]);

  const submitCurrent = useCallback((opts = {}) => {
    const snap = stateRef.current;
    const built = buildAnswer({
      taskType: snap.taskType,
      shot: snap.lastShot,
      shots: snap.trakeShots,
      qaAnswer: snap.qaAnswer,
      endPadMs: snap.endPadMs,
    });
    return sendBuilt(built, opts);
  }, [sendBuilt]);

  const confirmDuplicate = useCallback(() => {
    if (!pendingDuplicate) return;
    const built = pendingDuplicate;
    setPendingDuplicate(null);
    return sendBuilt(built, { dryRun: false, force: true });
  }, [pendingDuplicate, sendBuilt]);

  const removeTrakeAt = useCallback((index) => {
    setTrakeShots((prev) => prev.filter((_, i) => i !== index));
  }, []);

  const moveTrake = useCallback((index, delta) => {
    setTrakeShots((prev) => {
      const next = [...prev];
      const target = index + delta;
      if (target < 0 || target >= next.length) return prev;
      const [item] = next.splice(index, 1);
      next.splice(target, 0, item);
      return next;
    });
  }, []);

  const replaceTrakeShots = useCallback((shots) => {
    setTrakeShots(Array.isArray(shots) ? shots : []);
    setStatus({ level: 'ok', text: `TRAKE list: ${(shots || []).length} frame.` });
  }, []);

  const startQuestion = useCallback(() => {
    submittedRef.current = new Set();
    setSubmittedKeys([]);
    setWrongCount(0);
    setKFrozen(false);
    setLockedScore(null);
    setStartedAt(Date.now());
    setLastShot(null);
    setTrakeShots([]);
    setQaAnswer('');
    setPreview(null);
    setPendingDuplicate(null);
    setQuestionNonce((n) => n + 1);
    setStatus({ level: 'ok', text: 'Câu mới — timer đã chạy. k = 0.' });
    searchApi.current.ensureQueryName?.();
    if (stateRef.current.taskType === 'trake') {
      searchApi.current.setTemporalSearch?.(true);
      searchApi.current.setHybridSearch?.(false);
      searchApi.current.setTranslate?.(true);
      searchApi.current.setModelName?.('siglip2');
    }
  }, []);

  const markWrong = useCallback(() => {
    if (stateRef.current.kFrozen) return;
    setWrongCount((count) => count + 1);
  }, []);

  useEffect(() => {
    const onKey = (event) => {
      if (!event.altKey || event.key.toLowerCase() !== 's') return;
      event.preventDefault();
      if (stateRef.current.submitting) return;
      submitCurrent({ dryRun: false, force: false });
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [submitCurrent]);

  const draft = useMemo(() => buildAnswer({
    taskType,
    shot: lastShot,
    shots: trakeShots,
    qaAnswer,
    endPadMs,
  }), [taskType, lastShot, trakeShots, qaAnswer, endPadMs]);

  const value = useMemo(() => ({
    baseUrl,
    setBaseUrl,
    username,
    setUsername,
    password,
    setPassword,
    sessionId,
    taskType,
    setTaskType,
    qaAnswer,
    setQaAnswer,
    endPadMs,
    setEndPadMs,
    evaluation,
    evaluations,
    selectEvaluation,
    lastShot,
    trakeShots,
    wrongCount,
    kFrozen,
    lockedScore,
    startedAt,
    submitting,
    status,
    preview,
    draft,
    pendingDuplicate,
    submittedKeys,
    drawerSignal,
    questionNonce,
    registerSearchApi,
    setStatus,
    applyQuery,
    widenResults,
    enableFilter,
    connect,
    submitShot,
    submitCurrent,
    confirmDuplicate,
    removeTrakeAt,
    moveTrake,
    replaceTrakeShots,
    startQuestion,
    markWrong,
    askOpen,
    buildShot,
  }), [
    baseUrl,
    username,
    password,
    sessionId,
    taskType,
    setTaskType,
    qaAnswer,
    endPadMs,
    evaluation,
    evaluations,
    selectEvaluation,
    lastShot,
    trakeShots,
    wrongCount,
    kFrozen,
    lockedScore,
    startedAt,
    submitting,
    status,
    preview,
    draft,
    pendingDuplicate,
    submittedKeys,
    drawerSignal,
    questionNonce,
    registerSearchApi,
    setStatus,
    applyQuery,
    widenResults,
    enableFilter,
    connect,
    submitShot,
    submitCurrent,
    confirmDuplicate,
    removeTrakeAt,
    moveTrake,
    replaceTrakeShots,
    startQuestion,
    markWrong,
    askOpen,
  ]);

  return <DresContext.Provider value={value}>{children}</DresContext.Provider>;
};

export const useDres = () => {
  const ctx = useContext(DresContext);
  if (!ctx) {
    throw new Error('useDres must be used inside DresProvider');
  }
  return ctx;
};
