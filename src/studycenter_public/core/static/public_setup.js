(() => {
  'use strict';
  let installing = false;
  document.querySelectorAll('[data-component-install]').forEach(form => {
    form.addEventListener('submit', async event => {
      event.preventDefault();
      if (installing || !form.reportValidity()) return;
      installing = true;
      const buttons = [...document.querySelectorAll('[data-component-install] button')];
      const status = form.closest('[data-component-card]').querySelector('[data-component-status]');
      buttons.forEach(button => button.disabled = true);
      status.textContent = 'Wird heruntergeladen und eingerichtet … Das kann einige Minuten dauern.';
      try {
        await api('/api/components/install', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({component:form.dataset.componentInstall,allow_download:true})});
        const result = await api('/api/components');
        if (!result.components[form.dataset.componentInstall].installed) throw new Error(result.components[form.dataset.componentInstall].error || 'Einrichtung noch nicht abgeschlossen.');
        status.textContent = 'Bereit';
        form.remove();
      } catch (error) { status.textContent = error.message || 'Einrichtung fehlgeschlagen. Du kannst es erneut versuchen.'; }
      finally { installing = false; buttons.forEach(button => button.disabled = false); }
    });
  });
  const session = document.querySelector('[data-session-secrets]');
  const model = document.querySelector('[data-model-api]');
  model?.addEventListener('submit', async event => {
    event.preventDefault();
    const button = model.querySelector('button');
    if (button.disabled || !model.reportValidity()) return;
    button.disabled = true;
    const body = new FormData(model);
    model.elements.api_key.value = '';
    try { await api(model.action, {method:'POST',body}); model.querySelector('[role="status"]').textContent = 'Verbindung gespeichert. Unter „Luna verbinden“ kannst du sie auswählen und prüfen.'; }
    catch (error) { model.querySelector('[role="status"]').textContent = error.message; }
    finally { body.delete('api_key'); button.disabled = false; }
  });
  session?.addEventListener('submit', async event => {
    event.preventDefault();
    const button = session.querySelector('button');
    if (button.disabled || !session.reportValidity()) return;
    button.disabled = true;
    try { await api(session.action, {method:'POST',body:new FormData(session)}); location.reload(); }
    catch (error) { session.querySelector('[role="status"]').textContent = error.message; button.disabled = false; }
  });
})();
