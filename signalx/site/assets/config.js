// API base URL for the static site. Empty = same-origin (when serving site
// from the FastAPI backend). When the static site is hosted on devinapps
// and the backend lives on a separate Fly.io host, this gets rewritten at
// deploy time. Override with `window.SIGNALX_API = "https://api…";`
window.SIGNALX_API = window.SIGNALX_API || '';
