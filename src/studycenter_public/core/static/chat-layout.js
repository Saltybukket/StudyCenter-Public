(() => {
  const root = document.documentElement, panel = document.getElementById('voice-panel');
  const handle = document.getElementById('voice-resize');
  if (!panel || !handle) return;
  // Follow the actual header height, including wrapped controls and mobile search.
  const header = document.querySelector('.masthead');
  const navigation = document.querySelector('.layout > aside');
  function placeBelowHeader() {
    if (!header) return;
    let bottom = header.getBoundingClientRect().bottom;
    if (navigation && navigation.getBoundingClientRect().width >= innerWidth * 0.8) {
      bottom = Math.max(bottom, navigation.getBoundingClientRect().bottom);
    }
    root.style.setProperty('--chat-header-bottom', Math.max(0, Math.ceil(bottom)) + 'px');
  }
  const headerObserver = new ResizeObserver(placeBelowHeader);
  if (header) headerObserver.observe(header);
  if (navigation) headerObserver.observe(navigation);
  window.addEventListener('scroll', placeBelowHeader, {passive:true});
  window.addEventListener('resize', placeBelowHeader);
  placeBelowHeader();
  const save = (key, value) => { try { localStorage.setItem(key, value); } catch (_) {} };
  const sidebar = () => root.dataset.chatLayout === 'sidebar';
  function width(value, persist = false) {
    const max = Math.max(300, Math.min(600, innerWidth - 480));
    const size = Math.round(Math.max(300, Math.min(max, value)));
    root.style.setProperty('--chat-width', size + 'px');
    handle.setAttribute('aria-valuenow', size);
    handle.setAttribute('aria-valuemax', max);
    if (persist) save('campusboard.chat-width', size);
  }
  function apply(layout) {
    root.dataset.chatLayout = layout === 'sidebar' ? 'sidebar' : 'floating';
    document.querySelectorAll('[data-chat-layout-choice]').forEach(input => { input.checked = input.value === root.dataset.chatLayout; });
    width(parseFloat(root.style.getPropertyValue('--chat-width')) || 380);
  }
  apply(root.dataset.chatLayout);
  document.querySelectorAll('[data-chat-layout-choice]').forEach(input => input.addEventListener('change', () => {
    if (!input.checked) return;
    apply(input.value); save('campusboard.chat-layout', root.dataset.chatLayout);
  }));
  let dragging = false;
  handle.addEventListener('pointerdown', event => {
    if (event.button !== 0) return;
    event.preventDefault(); dragging = true; handle.setPointerCapture(event.pointerId);
    document.body.classList.add('chat-resizing');
  });
  handle.addEventListener('pointermove', event => { if (dragging) width(innerWidth - event.clientX); });
  const finish = () => { if (dragging) width(parseFloat(root.style.getPropertyValue('--chat-width')), true); dragging = false; document.body.classList.remove('chat-resizing'); };
  handle.addEventListener('pointerup', finish); handle.addEventListener('lostpointercapture', finish); handle.addEventListener('pointercancel', finish);
  handle.addEventListener('keydown', event => {
    if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
    event.preventDefault();
    width(event.key === 'Home' ? 300 : event.key === 'End' ? 600 : parseFloat(root.style.getPropertyValue('--chat-width')) + (event.key === 'ArrowLeft' ? 20 : -20), true);
  });
  window.addEventListener('resize', () => width(parseFloat(root.style.getPropertyValue('--chat-width'))));
  window.addEventListener('storage', event => { if (event.key === 'campusboard.chat-layout') apply(event.newValue); });
  new MutationObserver(() => { try { sessionStorage.setItem('campusboard.chat-open', String(panel.open)); } catch (_) {} }).observe(panel, {attributes:true,attributeFilter:['open']});
  try { if (sidebar() && sessionStorage.getItem('campusboard.chat-open') === 'true') document.getElementById('voice-open').click(); } catch (_) {}
})();
