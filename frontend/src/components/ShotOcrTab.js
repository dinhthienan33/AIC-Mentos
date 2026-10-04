import React, { useEffect, useRef, useState } from 'react';
import { apiUrl } from '../config';
import { submitOnEnter } from '../utils/submitOnEnter';

const fileFromList = (list) => {
  if (!list) return null;
  for (const item of list) {
    const file = item.getAsFile ? item.getAsFile() : item;
    if (file && (file.type || '').startsWith('image/')) return file;
  }
  return null;
};

const ShotOcrTab = ({ active, onUseQuery }) => {
  const [text, setText] = useState('');
  const [preview, setPreview] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [seconds, setSeconds] = useState(null);
  const [copied, setCopied] = useState(false);
  const inputRef = useRef(null);
  const zoneRef = useRef(null);

  useEffect(() => () => {
    if (preview.startsWith('blob:')) URL.revokeObjectURL(preview);
  }, [preview]);

  const recognize = async (file) => {
    if (!file) return;
    setError('');
    setCopied(false);
    setBusy(true);
    setPreview((prev) => {
      if (prev.startsWith('blob:')) URL.revokeObjectURL(prev);
      return URL.createObjectURL(file);
    });
    try {
      const body = new FormData();
      body.append('file', file, file.name || 'shot.png');
      const response = await fetch(apiUrl('/ocr-image'), { method: 'POST', body });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) {
        const detail = data.detail || `HTTP ${response.status}`;
        throw new Error(typeof detail === 'string' ? detail : JSON.stringify(detail));
      }
      setText(data.text || '');
      setSeconds(data.seconds ?? null);
    } catch (err) {
      setError(String(err?.message || err));
    } finally {
      setBusy(false);
    }
  };

  useEffect(() => {
    if (!active) return undefined;
    const onPaste = (event) => {
      const file = fileFromList(event.clipboardData?.items);
      if (!file) return;
      event.preventDefault();
      recognize(file);
    };
    window.addEventListener('paste', onPaste);
    return () => window.removeEventListener('paste', onPaste);
  }, [active]);

  const onDrop = (event) => {
    event.preventDefault();
    const file = fileFromList(event.dataTransfer?.files);
    if (file) recognize(file);
  };

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
    <div className="shot-ocr-tab">
      <h2>Ảnh đề → chữ</h2>
      <p className="description">
        Chụp màn hình đề, rồi Ctrl+V ngay trên tab này. Chữ hiện ra để copy hoặc đưa sang ô tìm hình.
      </p>

      <div
        ref={zoneRef}
        className={`shot-drop${busy ? ' is-busy' : ''}`}
        onDragOver={(event) => event.preventDefault()}
        onDrop={onDrop}
        onClick={() => inputRef.current?.click()}
        role="button"
        tabIndex={0}
        onKeyDown={(event) => {
          if (event.key === 'Enter' || event.key === ' ') inputRef.current?.click();
        }}
      >
        {preview ? <img src={preview} alt="Ảnh vừa dán" /> : <span>Ctrl+V để dán ảnh, hoặc kéo thả / bấm để chọn file</span>}
        <input
          ref={inputRef}
          type="file"
          accept="image/*"
          hidden
          onChange={(event) => {
            const file = event.target.files?.[0];
            event.target.value = '';
            if (file) recognize(file);
          }}
        />
      </div>

      {busy && <div className="loading-message">Đang đọc chữ trên ảnh…</div>}
      {error && <div className="error-message">Error: {error}</div>}
      {seconds != null && !busy && !error && (
        <div className="processing-time">Đọc xong trong {seconds.toFixed(2)} giây</div>
      )}

      <label className="shot-label">
        Chữ OCR
        <textarea
          value={text}
          onChange={(event) => setText(event.target.value)}
          onKeyDown={(event) => submitOnEnter(event, () => {
            if (text.trim() && onUseQuery) onUseQuery(text.trim());
          })}
          rows={12}
          placeholder="Chữ đọc được sẽ hiện ở đây. Sửa tay được trước khi tìm."
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

export default ShotOcrTab;
