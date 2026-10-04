export const formatKeyframeCsvLine = (videoId, frameNum) => `${videoId},${frameNum}`;

const triggerDownload = (blob, filename) => {
  const link = document.createElement('a');
  const url = URL.createObjectURL(blob);
  link.setAttribute('href', url);
  link.setAttribute('download', filename);
  link.style.visibility = 'hidden';
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
  URL.revokeObjectURL(url);
};

export const downloadKeyframesCsv = (lines, baseName) => {
  if (!lines.length) return;

  const csvContent = lines.join('\n');
  const blob = new Blob([csvContent], { type: 'text/csv;charset=utf-8;' });
  const base = (baseName && baseName.trim()) || 'keyframes';
  triggerDownload(blob, `${base}.csv`);
};

export const downloadJson = (data, baseName) => {
  const blob = new Blob([JSON.stringify(data, null, 2)], {
    type: 'application/json;charset=utf-8;',
  });
  const base = (baseName && baseName.trim()) || 'search-config';
  triggerDownload(blob, `${base}.json`);
};
