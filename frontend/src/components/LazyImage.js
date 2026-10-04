import React, { useEffect, useRef, useState } from 'react';
import { enqueueImageLoad } from '../utils/imageLoadQueue';

const preload = (src) =>
  new Promise((resolve, reject) => {
    const img = new Image();
    img.decoding = 'async';
    img.onload = () => resolve(src);
    img.onerror = () => reject(new Error('Image load failed'));
    img.src = src;
  });

const LazyImage = ({
  src,
  alt = '',
  className = '',
  wrapperClassName = '',
  eager = false,
  onClick,
  onError,
  fetchPriority,
}) => {
  const ref = useRef(null);
  const [displaySrc, setDisplaySrc] = useState(null);
  const [loaded, setLoaded] = useState(false);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    if (!src) return undefined;

    let cancelled = false;
    let observer;

    const load = () => {
      enqueueImageLoad(() => preload(src))
        .then(() => {
          if (!cancelled) {
            setDisplaySrc(src);
            setLoaded(true);
            setFailed(false);
          }
        })
        .catch(() => {
          if (!cancelled) {
            setFailed(true);
            onError?.();
          }
        });
    };

    if (eager) {
      load();
      return () => {
        cancelled = true;
      };
    }

    const el = ref.current;
    if (!el) return undefined;

    observer = new IntersectionObserver(
      (entries) => {
        if (entries[0]?.isIntersecting) {
          observer?.disconnect();
          load();
        }
      },
      { rootMargin: '240px', threshold: 0.01 }
    );

    observer.observe(el);

    return () => {
      cancelled = true;
      observer?.disconnect();
    };
  }, [src, eager, onError]);

  return (
    <div
      ref={ref}
      className={`lazy-image-wrap${wrapperClassName ? ` ${wrapperClassName}` : ''}${loaded ? ' lazy-image-wrap--loaded' : ''}${failed ? ' lazy-image-wrap--failed' : ''}${onClick ? ' lazy-image-wrap--clickable' : ''}`}
    >
      {!loaded && !failed && <div className="lazy-image-placeholder" aria-hidden="true" />}
      {displaySrc && (
        <img
          className={className}
          src={displaySrc}
          alt={alt}
          decoding="async"
          fetchPriority={fetchPriority}
          onClick={onClick}
        />
      )}
    </div>
  );
};

export default LazyImage;
