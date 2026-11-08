// Browser-local appearance only; never changes account or study data.
(() => {
  const key = 'campusboard.theme';
  const modes = new Set(['light', 'dark', 'system', 'sand', 'sage', 'lavender']);
  const system = window.matchMedia('(prefers-color-scheme: dark)');
  let mode = 'light';
  try { const saved = localStorage.getItem(key); if (modes.has(saved)) mode = saved; } catch (_) {}
  function apply() {
    document.documentElement.dataset.theme = mode === 'system' ? (system.matches ? 'dark' : 'light') : mode;
    document.querySelectorAll('[data-theme-choice]').forEach(input => { input.checked = input.value === mode; });
  }
  apply();
  window.addEventListener('storage', event => {
    if (event.key !== key && event.key !== null) return;
    mode = modes.has(event.newValue) ? event.newValue : 'light';
    apply();
  });
  system.addEventListener('change', () => { if (mode === 'system') apply(); });
  document.addEventListener('DOMContentLoaded', () => {
    apply();
    document.querySelectorAll('[data-theme-choice]').forEach(input => input.addEventListener('change', () => {
      if (!input.checked || !modes.has(input.value)) return;
      mode = input.value;
      try { localStorage.setItem(key, mode); } catch (_) {}
      apply();
    }));
  });
})();

// Independent of light/dark mode; applied before the first page paint.
(() => {
  const key = 'campusboard.wall-style', allowed = new Set(['cork', 'slate', 'plain']);
  let style = 'cork';
  try { const saved = localStorage.getItem(key); if (allowed.has(saved)) style = saved; } catch (_) {}
  function apply() {
    document.documentElement.dataset.wallStyle = style;
    document.querySelectorAll('[data-wall-style-choice]').forEach(input => { input.checked = input.value === style; });
  }
  apply();
  document.addEventListener('DOMContentLoaded', () => {
    apply();
    document.querySelectorAll('[data-wall-style-choice]').forEach(input => input.addEventListener('change', () => {
      if (!input.checked || !allowed.has(input.value)) return;
      style = input.value;
      try { localStorage.setItem(key, style); } catch (_) {}
      apply();
    }));
  });
  window.addEventListener('storage', event => {
    if (event.key !== key && event.key !== null) return;
    style = allowed.has(event.newValue) ? event.newValue : 'cork';
    apply();
  });
})();

(() => {
  let layout = 'floating', width = 380;
  try {
    if (localStorage.getItem('campusboard.chat-layout') === 'sidebar') layout = 'sidebar';
    const saved = Number(localStorage.getItem('campusboard.chat-width'));
    if (Number.isFinite(saved) && saved >= 300) width = Math.min(600, saved);
  } catch (_) {}
  document.documentElement.dataset.chatLayout = layout;
  document.documentElement.style.setProperty('--chat-width', width + 'px');
})();
