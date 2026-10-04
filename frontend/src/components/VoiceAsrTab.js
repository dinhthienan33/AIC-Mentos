import React, { useEffect, useRef, useState } from 'react';
import { apiUrl } from '../config';
import { submitOnEnter } from '../utils/submitOnEnter';

const pickAudio = (list) => {
  if (!list) return null;
  for (const item of list) {
    const file = item.getAsFile ? item.getAsFile() : item;
    if (!file) continue;
    const type = file.type || '';
    if (type.startsWith('audio/') || type.startsWith('video/') || /\.(webm|wav|mp3|m4a|ogg|mp4|flac)$/i.test(file.name || '')) {
      return file;
    }
  }
  return null;
};

const VoiceAsrTab = ({ active, onUseQuery }) => {
  const [text, setText] = useState('');
  const [busy, setBusy] = useState(false);
  const [recording, setRecording] = useState(false);
  const [error, setError] = useState('');
  const [seconds, setSeconds] = useState(null);
  const [copied, setCopied] = useState(false);
  const [language, setLanguage] = useState('vi');
  const inputRef = useRef(null);
  const recorderRef = useRef(null);
  const chunksRef = useRef([]);
  const streamRef = useRef(null);

  useEffect(() => () => {
    streamRef.current?.getTracks().forEach((track) => track.stop());
  }, []);

  const transcribe = async (file) => {
    if (!file) return;
    setError('');
    setCopied(false);
    setBusy(true);
    try {
      const body = new FormData();
      body.append('file', file, file.name || 'voice.webm');
      body.append('language', language);
      const response = await fetch(apiUrl(`/asr-transcribe?language=${encodeURIComponent(language)}`), {
        method: 'POST',
        body,
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) {
        const detail = data.detail || `HTTP ${response.status}`;
        throw new Error(typeof detail === 'string' ? detail : JSON.stringify(detail));
      }
      setText(data.text || '');
      setSeconds(data.seconds ?? null);
      if (!data.text) setError('Không nghe thấy lời nào trong đoạn này.');
    } catch (err) {
      setError(String(err?.message || err));
    } finally {
      setBusy(false);
    }
  };

  const stopTracks = () => {
    streamRef.current?.getTracks().forEach((track) => track.stop());
    streamRef.current = null;
  };

  const startRecording = async () => {
    setError('');
    if (!navigator.mediaDevices?.getUserMedia) {
      setError('Trình duyệt không cho ghi âm. Hãy tải file âm thanh.');
      return;
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      streamRef.current = stream;
      const mime = MediaRecorder.isTypeSupported('audio/webm;codecs=opus')
        ? 'audio/webm;codecs=opus'
        : (MediaRecorder.isTypeSupported('audio/webm') ? 'audio/webm' : '');
      const recorder = new MediaRecorder(stream, mime ? { mimeType: mime } : undefined);
      chunksRef.current = [];
      recorder.ondataavailable = (event) => {
        if (event.data && event.data.size > 0) chunksRef.current.push(event.data);
      };
      recorder.onstop = () => {
        const blob = new Blob(chunksRef.current, { type: recorder.mimeType || 'audio/webm' });
        stopTracks();
        setRecording(false);
        if (blob.size > 0) {
          transcribe(new File([blob], 'voice.webm', { type: blob.type }));
        }
      };
      recorderRef.current = recorder;
      recorder.start();
      setRecording(true);
    } catch (err) {
      stopTracks();
      setRecording(false);
      setError(String(err?.message || err));
    }
  };

  const stopRecording = () => {
    const recorder = recorderRef.current;
    if (recorder && recorder.state !== 'inactive') recorder.stop();
  };

  useEffect(() => {
    if (active) return undefined;
    if (recorderRef.current && recorderRef.current.state !== 'inactive') {
      recorderRef.current.stop();
    }
    return undefined;
  }, [active]);

  const copyText = async () => {
    if (!text) return;
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
    } catch {
      setError('Không copy được. Bôi đen ô chữ rồi Ctrl+C.');
    }
  };

  return (
    <div className="shot-ocr-tab voice-asr-tab">
      <h2>Giọng nói → chữ</h2>
      <p className="description">
        Bấm ghi âm, nói câu cần tìm, rồi dừng. Có thể tải file âm thanh. Chữ hiện ra để sửa, copy, hoặc đưa sang tìm hình.
      </p>

      <div className="voice-asr-controls">
        <label>
          Ngôn ngữ
          <select value={language} onChange={(event) => setLanguage(event.target.value)} disabled={busy || recording}>
            <option value="vi">Tiếng Việt</option>
            <option value="en">English</option>
            <option value="auto">Tự nhận</option>
          </select>
        </label>
        {!recording ? (
          <button type="button" onClick={startRecording} disabled={busy}>
            Ghi âm
          </button>
        ) : (
          <button type="button" className="voice-asr-stop" onClick={stopRecording}>
            Dừng và đọc
          </button>
        )}
        <button type="button" className="secondary" onClick={() => inputRef.current?.click()} disabled={busy || recording}>
          Tải file
        </button>
        <input
          ref={inputRef}
          type="file"
          accept="audio/*,video/webm,video/mp4"
          hidden
          onChange={(event) => {
            const file = pickAudio(event.target.files);
            event.target.value = '';
            if (file) transcribe(file);
          }}
        />
      </div>

      {recording && <div className="loading-message">Đang ghi âm… bấm Dừng khi nói xong.</div>}
      {busy && <div className="loading-message">Đang chuyển giọng nói thành chữ…</div>}
      {error && <div className="error-message">Error: {error}</div>}
      {seconds != null && !busy && !recording && !error && (
        <div className="processing-time">Đọc xong trong {seconds.toFixed(2)} giây</div>
      )}

      <label className="shot-label">
        Lời nói
        <textarea
          value={text}
          onChange={(event) => setText(event.target.value)}
          onKeyDown={(event) => submitOnEnter(event, () => {
            if (text.trim() && onUseQuery) onUseQuery(text.trim());
          })}
          rows={12}
          placeholder="Bản chữ sẽ hiện ở đây. Sửa tay được trước khi tìm."
        />
      </label>

      <div className="cctv-actions">
        <button type="button" onClick={copyText} disabled={!text}>
          {copied ? 'Đã copy' : 'Copy'}
        </button>
        <button
          type="button"
          className="secondary"
          disabled={!text.trim() || !onUseQuery}
          onClick={() => onUseQuery(text.trim())}
        >
          Đưa vào tìm hình
        </button>
      </div>
    </div>
  );
};

export default VoiceAsrTab;
