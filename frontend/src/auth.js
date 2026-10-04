export const AUTH_SESSION_KEY = 'visual-search-auth';

const VALID_USERNAME = process.env.REACT_APP_AUTH_USERNAME || '';
const VALID_PASSWORD = process.env.REACT_APP_AUTH_PASSWORD || '';

export const isAuthConfigured = () => Boolean(VALID_USERNAME && VALID_PASSWORD);

export const isAuthenticated = () => sessionStorage.getItem(AUTH_SESSION_KEY) === '1';

export const login = (username, password) => {
  if (!isAuthConfigured()) {
    return false;
  }
  if (username === VALID_USERNAME && password === VALID_PASSWORD) {
    sessionStorage.setItem(AUTH_SESSION_KEY, '1');
    return true;
  }
  return false;
};

export const logout = () => {
  sessionStorage.removeItem(AUTH_SESSION_KEY);
};
