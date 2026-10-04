import React, { useState } from 'react';
import { SITE_INFO } from '../siteInfo';
import { isAuthConfigured } from '../auth';

const LoginPage = ({ onLogin }) => {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);

  const handleSubmit = (event) => {
    event.preventDefault();
    setError('');
    setLoading(true);

    if (!isAuthConfigured()) {
      setError('Login is not configured. Set REACT_APP_AUTH_USERNAME and REACT_APP_AUTH_PASSWORD.');
      setLoading(false);
      return;
    }

    const ok = onLogin(username.trim(), password);
    if (!ok) {
      setError('Invalid username or password.');
      setLoading(false);
    }
  };

  return (
    <div className="login-page">
      <div className="login-card">
        <div className="login-brand">
          <h1>{SITE_INFO.title}</h1>
          <p>{SITE_INFO.team}</p>
        </div>

        <form className="login-form" onSubmit={handleSubmit}>
          <label className="login-label" htmlFor="login-username">Username</label>
          <input
            id="login-username"
            className="login-input"
            type="text"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            placeholder="Enter username"
            autoComplete="username"
            required
          />

          <label className="login-label" htmlFor="login-password">Password</label>
          <input
            id="login-password"
            className="login-input"
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            placeholder="Enter password"
            autoComplete="current-password"
            required
          />

          {error && <div className="login-error">{error}</div>}

          <button className="login-submit" type="submit" disabled={loading}>
            {loading ? 'Signing in…' : 'Sign in'}
          </button>
        </form>
      </div>
    </div>
  );
};

export default LoginPage;
