// Post-it status buttons use the shared quick-form submission and its result.
document.querySelectorAll('[data-postit-status]').forEach(form => {
  const status = form.querySelector('input[name="status"]');
  const submit = form.querySelector('.postit-submit');
  const choices = [...form.querySelectorAll('[data-status-choice]')];
  let saved = status.value, busy = false;
  status.disabled = false;
  choices.forEach(button => {
    button.type = 'button';
    button.removeAttribute('name');
    button.addEventListener('click', () => {
      if (busy || button.dataset.statusChoice === saved) return;
      busy = true;
      status.value = button.dataset.statusChoice;
      form.setAttribute('aria-busy', 'true');
      choices.forEach(choice => { choice.disabled = true; });
      form.requestSubmit(submit);
    });
  });
  form.addEventListener('quick-form-result', event => {
    if (event.detail.ok) saved = status.value;
    else status.value = saved;
    busy = false;
    form.removeAttribute('aria-busy');
    choices.forEach(choice => {
      choice.disabled = false;
      choice.setAttribute('aria-pressed', String(choice.dataset.statusChoice === saved));
    });
  });
});

// Keep the filter usable when its panel covers the summary on a small screen.
document.querySelectorAll('.wall-filter-disclosure').forEach(disclosure => {
  const summary = disclosure.querySelector('summary');
  const close = () => { disclosure.open = false; summary.focus(); };
  disclosure.querySelector('[data-filter-close]')?.addEventListener('click', close);
  disclosure.addEventListener('keydown', event => {
    if (event.key === 'Escape' && disclosure.open) { event.preventDefault(); close(); }
  });
  document.addEventListener('click', event => {
    if (disclosure.open && !disclosure.contains(event.target)) disclosure.open = false;
  });
});

// Lightweight disclosures close outside; composing text is deliberately excluded.
document.addEventListener('click', event => {
  document.querySelectorAll('.sticky-note .tag[open], .login-menu[open]').forEach(panel => {
    if (!panel.contains(event.target)) panel.open = false;
  });
});
function revealEntrySection() {
  const target = document.getElementById(location.hash.slice(1));
  if (!target || !['edit', 'reminders'].includes(target.id)) return;
  for (let parent = target; parent; parent = parent.parentElement) {
    if (parent.tagName === 'DETAILS') parent.open = true;
  }
  if (target.id === 'reminders') target.querySelector('details').open = true;
  target.scrollIntoView({block:'start'});
}
window.addEventListener('hashchange', revealEntrySection);
revealEntrySection();
