(() => {
  'use strict';
  const root = document.querySelector('#introduction');
  if (!root) return;
  const card = root.querySelector('.intro-card');
  const next = root.querySelector('.intro-next');
  const back = root.querySelector('.intro-back');
  const skip = root.querySelector('.intro-skip');
  const close = root.querySelector('.intro-close');
  const error = root.querySelector('.intro-error');
  const tryButton = root.querySelector('.intro-try');
  let inertBefore = [];
  const sessionKey = 'studycenter-introduction-dismissed-v1';
  const steps = [
    {path:'/sources', target:'.course-caption', action:'form[action="/courses"]', actionLabel:'Kursformular öffnen', kicker:'Dein eigener Anfang', title:'Deine Kurse verbinden', text:'Melde dich selbst im TU-Browser an und richte deine Kurse und Quellen ein. Wenn du bereit bist, kannst du die Quellen selbst aktualisieren.', detail:'Die Einführung öffnet keine Anmeldung und startet keinen Abruf. Deine Passwörter und Bestätigungsschritte gibst nur du ein.'},
    {path:'/overview', target:'#actions', kicker:'Dein Tag', title:'Das Wichtigste im Blick', text:'„Heute“ sammelt wichtige offene Aufgaben, Fristen und Termine an einem Ort.', detail:'Prüfe unklare Angaben und ihre Belege. Zu Beginn ist diese Ansicht noch leer, bis du eigene Daten hinzufügst oder Quellen aktualisierst.'},
    {path:'/board', target:'.note-compose summary', action:'.note-compose', actionLabel:'Eigenes Post-it öffnen', kicker:'Deine Aufgaben', title:'Platz für deinen Überblick', text:'Hier findest du offene Einträge. Probiere ein eigenes Post-it aus: Titel eingeben und selbst anheften, wenn du möchtest.', detail:'Deine eigenen Änderungen und dein Bearbeitungsstand bleiben erhalten.'},
    {path:'/calendar', target:'.calendar-views', kicker:'Deine Zeit', title:'Termine und Fristen zusammen', text:'Im Kalender siehst du gespeicherte Termine und Fristen in ihrem zeitlichen Zusammenhang.', detail:'Ein angebotenes Zeitfenster ist noch keine persönliche Buchung. Schau bei offenen Fragen in den jeweiligen Beleg.'},
    {path:'/quests', target:'#quest-create', kicker:'In deinem Tempo', title:'Mit Studi und Quests planen', text:'Quests helfen dir, ein Lernziel in kleinere Schritte zu teilen. Studi kann dich dabei unterstützen, wenn du ein Modell verbindest.', detail:'KI ist freiwillig. Bei ihrer Nutzung können Inhalte an den gewählten Anbieter übermittelt werden und Kosten entstehen.'}
  ];
  let state = null, previousFocus = null, highlighted = null, busy = false;
  const token = () => document.querySelector('meta[name="csrf-token"]')?.content || '';
  async function save(status, step) {
    return api('/api/introduction', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({status, step})});
  }
  function focusables() { return [...card.querySelectorAll('button:not([hidden]):not(:disabled)')]; }
  function clearHighlight() { if (highlighted) highlighted.classList.remove('intro-highlight'); highlighted = null; }
  function display() {
    const welcome = state.status === 'new';
    const step = steps[state.step];
    clearHighlight();
    root.classList.toggle('has-target', !welcome);
    root.querySelector('#intro-progress').textContent = welcome ? 'Willkommen' : `${state.step + 1} / ${steps.length}`;
    root.querySelector('#intro-kicker').textContent = welcome ? 'Schön, dass du da bist' : step.kicker;
    root.querySelector('#intro-title').textContent = welcome ? 'Willkommen bei StudyCenter' : step.title;
    root.querySelector('#intro-description').textContent = welcome ? 'Dein Studium, übersichtlich an einem Ort. Wir zeigen dir kurz, wo du anfangen kannst.' : step.text;
    root.querySelector('#intro-detail').textContent = welcome ? 'Du startest mit deinen eigenen Daten. Die Tour ist freiwillig und lässt sich in den Einstellungen erneut öffnen.' : step.detail;
    next.innerHTML = welcome ? 'Tour starten <span aria-hidden="true">→</span>' : state.step === steps.length - 1 ? 'Fertig' : 'Weiter <span aria-hidden="true">→</span>';
    back.hidden = welcome || state.step === 0;
    tryButton.hidden = welcome || !step.action || location.pathname !== step.path;
    tryButton.textContent = step.actionLabel || '';
    skip.textContent = welcome ? 'Jetzt nicht' : 'Tour beenden';
    error.hidden = true;
    if (!welcome && location.pathname === step.path && !matchMedia('(max-width: 650px)').matches) {
      highlighted = document.querySelector(step.target);
      if (highlighted && highlighted.getClientRects().length) highlighted.classList.add('intro-highlight');
    }
    if (root.hidden) {
      previousFocus = document.activeElement;
      inertBefore = [...document.body.children].filter(el => el !== root && el.tagName !== 'SCRIPT').map(el => [el, el.inert]);
      inertBefore.forEach(([el]) => { el.inert = true; });
      root.hidden = false;
    }
    card.focus();
  }
  function hide() { clearHighlight(); root.hidden = true; inertBefore.forEach(([el, wasInert]) => { el.inert = wasInert; }); inertBefore = []; if (previousFocus?.isConnected) previousFocus.focus(); }
  async function transition(status, step, navigate) {
    if (busy) return;
    busy = true; next.disabled = back.disabled = skip.disabled = close.disabled = true;
    try {
      state = await save(status, step);
      if (status === 'dismissed' || status === 'completed') { try { sessionStorage.removeItem(sessionKey); } catch (_) {} }
      if (navigate && location.pathname !== steps[step].path) location.assign(steps[step].path);
      else if (status === 'active') display();
      else hide();
    } catch (_) {
      if (status === 'dismissed') {
        try { sessionStorage.setItem(sessionKey, '1'); } catch (_) {}
        hide();
      } else { error.textContent = 'Die Tour konnte nicht gespeichert werden. Bitte versuche es erneut oder schließe sie.'; error.hidden = false; }
    } finally { busy = false; next.disabled = back.disabled = skip.disabled = close.disabled = false; }
  }
  next.addEventListener('click', () => {
    if (state.status === 'new') transition('active', 0, true);
    else if (state.step === steps.length - 1) transition('completed', state.step, false);
    else transition('active', state.step + 1, true);
  });
  function addResume() {
    if (document.querySelector('.intro-resume')) return;
    const button = document.createElement('button'); button.type = 'button'; button.className = 'intro-resume'; button.textContent = 'Einführung fortsetzen';
    button.addEventListener('click', async () => { await transition('active', state.step, true); if (state.status === 'active') button.remove(); });
    document.body.append(button);
  }
  tryButton.addEventListener('click', async () => {
    if (busy) return;
    busy = true; tryButton.disabled = true;
    try {
      state = await save('paused', state.step);
      hide(); addResume();
      const target = document.querySelector(steps[state.step].action);
      if (target) {
        for (let el = target; el; el = el.parentElement) if (el.tagName === 'DETAILS') el.open = true;
        target.scrollIntoView({block:'center', behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth'});
        (target.querySelector('input:not([type=hidden])') || target.querySelector('summary') || target).focus({preventScroll:true});
      }
    } catch (_) { error.textContent = 'Das Formular konnte noch nicht geöffnet werden. Bitte versuche es erneut.'; error.hidden = false; }
    finally { busy = false; tryButton.disabled = false; }
  });
  back.addEventListener('click', () => transition('active', Math.max(0, state.step - 1), true));
  const dismiss = () => transition('dismissed', state?.step || 0, false);
  skip.addEventListener('click', dismiss); close.addEventListener('click', dismiss);
  document.addEventListener('keydown', event => {
    if (root.hidden) return;
    if (event.key === 'Escape') { event.preventDefault(); dismiss(); }
    if (event.key === 'Tab') {
      const elements = focusables(); if (!elements.length) return;
      if (event.shiftKey && (document.activeElement === elements[0] || document.activeElement === card)) { event.preventDefault(); elements[elements.length - 1].focus(); }
      else if (!event.shiftKey && document.activeElement === elements[elements.length - 1]) { event.preventDefault(); elements[0].focus(); }
    }
  });
  if (location.pathname === '/settings') {
    const link = document.createElement('a'); link.href = '/introduction'; link.className = 'intro-replay'; link.textContent = 'Einführung erneut ansehen →';
    document.querySelector('#main')?.append(link);
  }
  function dismissedInSession() { try { return !!sessionStorage.getItem(sessionKey); } catch (_) { return false; } }
  (async () => {
    try {
      const response = await fetch('/api/introduction', {cache:'no-store'});
      if (!response.ok) return;
      state = await response.json();
      const url = new URL(location.href);
      if (url.searchParams.get('introduction') === 'start') {
        url.searchParams.delete('introduction'); history.replaceState(null, '', url.pathname + url.search + url.hash);
        state = {...state, status:'new', step:0}; display();
      } else if (state.status === 'paused') addResume();
      else if ((state.status === 'new' || state.status === 'active') && !dismissedInSession()) display();
    } catch (_) { /* The rest of StudyCenter remains usable if the tour is unavailable. */ }
  })();
})();
