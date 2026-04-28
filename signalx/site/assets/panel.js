// Shared helpers for /app, /manager, /admin pages.
window.SX = (function () {
  const STORAGE_KEY = 'signalx_token';
  const API = window.SIGNALX_API || '';

  function getToken() { return localStorage.getItem(STORAGE_KEY) || ''; }
  function setToken(t) { localStorage.setItem(STORAGE_KEY, t || ''); }
  function clearToken() { localStorage.removeItem(STORAGE_KEY); }

  async function api(path, opts = {}) {
    const headers = Object.assign({'Content-Type': 'application/json'}, opts.headers || {});
    const tok = getToken();
    if (tok) headers['Authorization'] = 'Bearer ' + tok;
    const res = await fetch(API + path, Object.assign({}, opts, { headers }));
    if (res.status === 401) {
      clearToken();
      if (!window.SX_NO_REDIRECT) window.location.href = '/login.html?return=' + encodeURIComponent(window.location.pathname);
      throw new Error('unauthorized');
    }
    if (!res.ok) {
      let msg = 'HTTP ' + res.status;
      try { const body = await res.json(); if (body.detail) msg = body.detail; } catch {}
      throw new Error(msg);
    }
    if (res.status === 204) return null;
    return res.json();
  }

  async function me() { return api('/auth/me'); }
  function logout() { clearToken(); window.location.href = '/login.html'; }

  function fmtDt(iso) {
    if (!iso) return '—';
    const d = new Date(iso);
    return d.toLocaleString('en-US', {month:'short', day:'numeric', hour:'2-digit', minute:'2-digit'});
  }
  function fmtMoney(v) {
    if (v == null) return '—';
    const n = Number(v);
    return (n >= 0 ? '+' : '') + n.toFixed(2);
  }
  function badge(type, label) { return `<span class="badge badge-${type}">${label}</span>`; }

  return { api, me, logout, getToken, setToken, clearToken, fmtDt, fmtMoney, badge };
})();
