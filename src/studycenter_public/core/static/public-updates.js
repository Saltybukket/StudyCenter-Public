(() => {
  const el = id => document.getElementById(id);
  const panel = el('updates'), banner = el('update-banner');
  let status = null, busy = false, timer, hiddenVersion = '', localError = '', installAccepted = false, consentVersion = '';
  const labels = {idle:'Noch nicht geprüft.', checking:'Suche nach Updates …', available:'Ein Update ist verfügbar.', current:'Du verwendest die aktuelle Version.', downloading:'Update wird heruntergeladen …', ready:'Update wurde heruntergeladen.', installing:'Installation und Neustart werden vorbereitet …', error:'Die Update-Aktion ist fehlgeschlagen.'};
  function dismissed(version) { try { return sessionStorage.getItem('studycenter.update.dismissed') === version; } catch (_) { return hiddenVersion === version; } }
  function dismiss(version) { hiddenVersion = version; try { sessionStorage.setItem('studycenter.update.dismissed', version); } catch (_) {} }
  function checkedAt(value) {
    if (!value) return '';
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? '' : ` Zuletzt geprüft: ${new Intl.DateTimeFormat('de-AT', {dateStyle:'medium', timeStyle:'short'}).format(date)}.`;
  }
  function releaseUrl(value) {
    if (typeof value !== 'string' || !value.startsWith('https://github.com/Saltybukket/StudyCenter-Public/releases/tag/')) return '';
    try { const url = new URL(value); return url.protocol === 'https:' && url.hostname === 'github.com' && !url.port && !url.username && !url.password && url.pathname.startsWith('/Saltybukket/StudyCenter-Public/releases/tag/') && url.pathname.length > '/Saltybukket/StudyCenter-Public/releases/tag/'.length ? url.href : ''; } catch (_) { return ''; }
  }
  function render() {
    if (!status) return;
    const release = status.release || {}, version = release.version || '';
    const active = installAccepted || ['checking','downloading','installing'].includes(status.state);
    const available = !!version && ['available','downloading','ready'].includes(status.state);
    const skipped = !!version && status.skipped_version === version;
    if (banner) {
      banner.hidden = !available || skipped || dismissed(version) || !!panel;
      el('update-banner-text').textContent = `StudyCenter ${version} ist verfügbar.`;
    }
    if (!panel) return;
    if (consentVersion !== version) { el('update-confirm').checked = false; consentVersion = version; }
    el('update-current').textContent = `Installierte Version: ${status.current_version || 'unbekannt'}${status.platform ? ' · ' + status.platform : ''}`;
    el('update-auto').checked = !!status.auto_check;
    el('update-auto').disabled = busy || !status.supported;
    el('update-check').disabled = busy || active || !status.supported;
    el('update-status').textContent = installAccepted ? 'Installation vorbereitet. Die App wird geschlossen und neu gestartet; bitte einen Moment warten.' : !status.supported ? 'Updates werden auf dieser Installation nicht unterstützt.' : (labels[status.state] || 'Update-Status unbekannt.') + checkedAt(status.last_checked);
    const outcomes = {installed:'Das letzte Update wurde installiert.', installer_completed:'Das Installationsprogramm wurde abgeschlossen.', cancelled_or_failed:'Die letzte Installation wurde abgebrochen oder ist fehlgeschlagen.', failed:'Die letzte Installation ist fehlgeschlagen.', aborted:'Die letzte Installation wurde abgebrochen.', timeout:'Die letzte Installation konnte nicht abgeschlossen werden.', pending:'Das letzte Update wird noch verarbeitet.'};
    const outcome = status.outcome || {};
    el('update-outcome').textContent = !installAccepted && outcomes[outcome.status] ? outcomes[outcome.status] + (outcome.detail ? ' ' + outcome.detail : '') : '';
    el('update-error').textContent = localError || status.error || '';
    el('update-release').hidden = !available;
    el('update-version').textContent = `Version ${version}`;
    el('update-skipped').hidden = !skipped;
    el('update-skip').hidden = skipped;
    el('update-notes').textContent = release.notes || 'Keine Versionshinweise verfügbar.';
    const link = el('update-release-link'), url = releaseUrl(release.url);
    link.hidden = !url; if (url) link.href = url; else link.removeAttribute('href');
    el('update-download').hidden = status.state === 'ready';
    el('update-download').disabled = busy || active;
    el('update-skip').disabled = busy || active;
    el('update-later').disabled = busy;
    el('update-install-area').hidden = !available || status.state !== 'ready';
    el('update-install-reason').textContent = status.can_install === false ? (status.install_reason || 'Installation ist hier nicht verfügbar.') : '';
    el('update-install').disabled = busy || installAccepted || status.state !== 'ready' || status.can_install === false || !el('update-confirm').checked;
  }
  async function refresh() {
    try { status = await api('/api/updates'); if (installAccepted && status.state !== 'installing') { installAccepted = false; if (panel) el('update-confirm').checked = false; } render(); }
    catch (error) { if (panel && !installAccepted) { el('update-error').textContent = error.message; el('update-current').textContent = 'Update-Status nicht verfügbar.'; } }
    clearTimeout(timer);
    timer = setTimeout(refresh, installAccepted || (status && ['checking','downloading','installing'].includes(status.state)) ? 2000 : 60000);
  }
  async function action(path, payload) {
    if (busy) return;
    busy = true; localError = ''; render();
    try { await api('/api/updates/' + path, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(payload)}); if (path === 'install') installAccepted = true; await refresh(); }
    catch (error) { localError = error.message; }
    finally { busy = false; render(); }
  }
  if (banner) el('update-banner-dismiss').addEventListener('click', () => { if (status?.release?.version) dismiss(status.release.version); banner.hidden = true; });
  if (panel) {
    el('update-check').addEventListener('click', () => action('check', {}));
    el('update-auto').addEventListener('change', e => action('preferences', {auto_check:e.target.checked}));
    el('update-skip').addEventListener('click', () => action('skip', {version:status.release.version}));
    el('update-download').addEventListener('click', () => action('download', {version:status.release.version}));
    el('update-later').addEventListener('click', () => { dismiss(status.release.version); location.href = '/settings?section=background'; });
    el('update-confirm').addEventListener('change', render);
    el('update-install').addEventListener('click', () => { if (!el('update-install').disabled) action('install', {version:status.release.version, confirm:true}); });
  }
  refresh();
})();
