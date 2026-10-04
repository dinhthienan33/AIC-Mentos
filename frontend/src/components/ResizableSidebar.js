import React, { useCallback, useEffect, useRef, useState } from 'react';

const STORAGE_WIDTH_KEY = 'visual-search-sidebar-width';
const STORAGE_COLLAPSED_KEY = 'visual-search-sidebar-collapsed';
const DEFAULT_WIDTH = 300;
const MIN_WIDTH = 240;
const MAX_WIDTH = 520;

const ResizableSidebar = ({ children }) => {
  const [width, setWidth] = useState(() => {
    const saved = Number(localStorage.getItem(STORAGE_WIDTH_KEY));
    return Number.isFinite(saved) && saved >= MIN_WIDTH ? saved : DEFAULT_WIDTH;
  });
  const [collapsed, setCollapsed] = useState(
    () => localStorage.getItem(STORAGE_COLLAPSED_KEY) === '1'
  );
  const dragging = useRef(false);
  const startX = useRef(0);
  const startWidth = useRef(width);
  const widthRef = useRef(width);

  useEffect(() => {
    widthRef.current = width;
  }, [width]);

  const startResize = useCallback((event) => {
    if (collapsed) return;
    event.preventDefault();
    dragging.current = true;
    startX.current = event.clientX;
    startWidth.current = widthRef.current;
    document.body.classList.add('sidebar-resizing');
  }, [collapsed]);

  useEffect(() => {
    const onMouseMove = (event) => {
      if (!dragging.current) return;
      const next = Math.min(
        MAX_WIDTH,
        Math.max(MIN_WIDTH, startWidth.current + (event.clientX - startX.current))
      );
      setWidth(next);
    };

    const onMouseUp = () => {
      if (!dragging.current) return;
      dragging.current = false;
      document.body.classList.remove('sidebar-resizing');
      localStorage.setItem(STORAGE_WIDTH_KEY, String(widthRef.current));
    };

    window.addEventListener('mousemove', onMouseMove);
    window.addEventListener('mouseup', onMouseUp);
    return () => {
      window.removeEventListener('mousemove', onMouseMove);
      window.removeEventListener('mouseup', onMouseUp);
      document.body.classList.remove('sidebar-resizing');
    };
  }, []);

  const toggleCollapsed = () => {
    setCollapsed((prev) => {
      const next = !prev;
      localStorage.setItem(STORAGE_COLLAPSED_KEY, next ? '1' : '0');
      return next;
    });
  };

  return (
    <div
      className={`sidebar-shell${collapsed ? ' sidebar-shell--collapsed' : ''}`}
      style={{ width: collapsed ? 0 : width }}
    >
      <aside className="sidebar" style={{ width: collapsed ? 0 : width }}>
        <div className="sidebar-inner">{children}</div>
        {!collapsed && (
          <div
            className="sidebar-resizer"
            onMouseDown={startResize}
            role="separator"
            aria-orientation="vertical"
            aria-label="Resize sidebar"
            title="Drag to resize sidebar"
          />
        )}
      </aside>
      <button
        type="button"
        className={`sidebar-toggle${collapsed ? ' sidebar-toggle--expand' : ''}`}
        onClick={toggleCollapsed}
        aria-label={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
        title={collapsed ? 'Show controls' : 'Hide controls'}
      >
        {collapsed ? '›' : '‹'}
      </button>
    </div>
  );
};

export default ResizableSidebar;
