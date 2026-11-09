/* Theme before first paint: the saved choice, else the system's dark preference, else light. */
(() => {
  const KEY = 'wiz-theme';
  const root = document.documentElement;
  let saved = null;
  try { saved = localStorage.getItem(KEY); } catch (e) { /* storage blocked: follow the system */ }
  const system = () => (window.matchMedia && matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light');
  const apply = (t) => { if (t === 'dark') root.dataset.theme = 'dark'; else delete root.dataset.theme; };
  apply(saved || system());
  const label = (b) => {
    const dark = root.dataset.theme === 'dark';
    b.textContent = dark ? '☀' : '☾';
    b.setAttribute('aria-label', dark ? 'Switch to light theme' : 'Switch to dark theme');
    b.title = b.getAttribute('aria-label');
  };
  document.addEventListener('DOMContentLoaded', () => {
    for (const b of document.querySelectorAll('[data-theme-toggle]')) {
      label(b);
      b.addEventListener('click', () => {
        const next = root.dataset.theme === 'dark' ? 'light' : 'dark';
        apply(next);
        try { localStorage.setItem(KEY, next); } catch (e) { /* not remembered */ }
        document.querySelectorAll('[data-theme-toggle]').forEach(label);
      });
    }
  });
})();
