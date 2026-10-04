import React from 'react';
import { SITE_INFO } from '../siteInfo';

const SiteFooter = () => {
  const { authors, team, year } = SITE_INFO;

  return (
    <footer className="site-footer">
      <span className="site-footer-team">{team}</span>
      <span className="site-footer-sep">·</span>
      <span className="site-footer-label">Author:</span>
      {authors.map((author, index) => (
        <React.Fragment key={author.url || author.name}>
          {index > 0 && <span className="site-footer-sep">,</span>}
          {author.url ? (
            <a className="site-footer-link" href={author.url} target="_blank" rel="noopener noreferrer">
              {author.name}
            </a>
          ) : (
            <span className="site-footer-author">{author.name}</span>
          )}
        </React.Fragment>
      ))}
      <span className="site-footer-sep">·</span>
      <span className="site-footer-year">{year}</span>
    </footer>
  );
};

export default SiteFooter;
