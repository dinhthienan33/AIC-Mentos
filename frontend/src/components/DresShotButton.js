import React from 'react';
import { useDres } from '../dres/DresContext';
import { buildShot } from '../dres/format';

const DresShotButton = ({ videoId, frame, fps, rank = null, source = 'result' }) => {
  const { taskType, submitting, submitShot } = useDres();
  const shot = buildShot({ videoId, frame, fps, rank, source });
  const missingFps = shot.ms == null;
  const trake = taskType === 'trake';
  const label = missingFps
    ? 'DRES thiếu fps'
    : trake
      ? `＋ TRAKE ${shot.frame}`
      : `Nộp ${shot.ms} ms`;

  return (
    <>
      {shot.rank != null && shot.rank <= 20 && !trake && (
        <div className="dres-early">Top {shot.rank} — lấy frame giữa đoạn</div>
      )}
      <button
        type="button"
        className="dres-shot-btn"
        disabled={missingFps || submitting}
        title={missingFps
          ? 'Không có fps metadata của video — không đoán fps'
          : trake
            ? 'Thêm frame này vào chuỗi TRAKE (đúng thứ tự bấm)'
            : 'Nộp video và mốc millisecond này lên DRES'}
        onClick={(event) => {
          event.stopPropagation();
          submitShot(shot);
        }}
      >
        {label}
      </button>
    </>
  );
};

export default DresShotButton;
