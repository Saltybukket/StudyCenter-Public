let token = document.querySelector('meta[name="csrf-token"]').content;
let tokenRefresh = null;
document.querySelectorAll('.reader-tabs').forEach(tabs=>{
  const buttons=[...tabs.querySelectorAll('[role="tab"]')];
  const select=button=>buttons.forEach(option=>{const active=option===button;option.setAttribute('aria-selected',String(active));option.tabIndex=active?0:-1;document.getElementById(option.getAttribute('aria-controls')).hidden=!active;});
  buttons.forEach((button,index)=>{button.addEventListener('click',()=>select(button));button.addEventListener('keydown',event=>{if(!['ArrowLeft','ArrowRight'].includes(event.key))return;event.preventDefault();const target=buttons[(index+(event.key==='ArrowRight'?1:buttons.length-1))%buttons.length];select(target);target.focus();});});
});
const tunnelForm=document.querySelector('#tunnel-settings-form');
if(tunnelForm){
  let tunnelBusy=false;
  function showTunnel(value){
    document.querySelector('#tunnel-state').textContent=value.state;
    document.querySelector('#tunnel-state').dataset.connected=String(Boolean(value.connected));
    const checked=value.checked_at?new Date(value.checked_at*1000).toLocaleTimeString('de-AT'): 'ausstehend';
    document.querySelector('#tunnel-checks').textContent=`OpenAI: ${value.openai?'erreichbar':'nicht bestätigt'} · MCP: ${value.mcp?'erreichbar':'nicht bestätigt'} · geprüft ${checked}`;
    document.querySelector('#tunnel-error').textContent=value.error||'';
    document.querySelector('#tunnel-key-state').textContent=value.key_saved?'(verschlüsselt gespeichert)':'(noch nicht gespeichert)';
    document.querySelector('#tunnel-takeover').hidden=!value.external;
    document.querySelector('#tunnel-manual').hidden=!value.external;
    const link=document.querySelector('#tunnel-admin');link.hidden=!value.ui_url;if(value.ui_url)link.href=value.ui_url;
  }
  async function tunnelStatus(){if(tunnelBusy||document.hidden)return;try{showTunnel(await api('/settings/tunnel/status'));}catch(_){document.querySelector('#tunnel-state').textContent='CampusBoard momentan nicht erreichbar';}}
  tunnelForm.addEventListener('submit',async event=>{
    event.preventDefault();if(tunnelBusy)return;tunnelBusy=true;
    const body=new FormData(tunnelForm);tunnelForm.elements.runtime_key.value='';
    try{const result=await api(tunnelForm.action,{method:'POST',body});tell(result.message);}
    catch(error){tell(error.message,true);}
    finally{body.delete('runtime_key');tunnelBusy=false;await tunnelStatus();}
  });
  document.querySelectorAll('[data-tunnel-action]').forEach(button=>button.addEventListener('click',async()=>{
    if(tunnelBusy)return;tunnelBusy=true;document.querySelectorAll('[data-tunnel-action]').forEach(b=>b.disabled=true);
    document.querySelector('#tunnel-state').textContent='Tunnel-Aktion läuft …';
    try{showTunnel(await api('/settings/tunnel/'+button.dataset.tunnelAction,{method:'POST'}));}
    catch(error){tell(error.message,true);}
    finally{tunnelBusy=false;document.querySelectorAll('[data-tunnel-action]').forEach(b=>b.disabled=false);await tunnelStatus();}
  }));
  setTimeout(tunnelStatus,0);setInterval(tunnelStatus,10000);
}
let appRestarting=false;
document.querySelectorAll('[data-app-restart]').forEach(form=>form.addEventListener('submit',async event=>{
  event.preventDefault();if(appRestarting)return;appRestarting=true;
  const forms=[...document.querySelectorAll('[data-app-restart]')];
  const report=message=>forms.forEach(f=>f.querySelector('.restart-progress').textContent=message);
  forms.forEach(f=>f.querySelector('button').disabled=true);
  report('Neustart wird vorbereitet …');
  try{
    const result=await api(form.action,{method:'POST',body:new FormData(form)});report(result.message);
    const until=Date.now()+90000;
    while(Date.now()<until){
      await new Promise(resolve=>setTimeout(resolve,1500));
      try{
        const response=await fetch('/api/status',{cache:'no-store',signal:AbortSignal.timeout(3000)});
        if(!response.ok)continue;const state=await response.json();
        if(state.instance_id&&state.instance_id!==result.instance_id){window.history.replaceState(window.history.state,'','/settings#background');location.reload();return;}
      }catch(_){}
    }
    report('Noch keine neue App-Verbindung. Seite nach dem lokalen Start neu laden. Ein automatischer Quellen-Sync wird nicht ausgelöst.');
  }catch(error){report(error.message);}
  finally{appRestarting=false;forms.forEach(f=>f.querySelector('button').disabled=false);}
}));
document.querySelector('#app-stop-form')?.addEventListener('submit',async event=>{event.preventDefault();try{const result=await api(event.currentTarget.action,{method:'POST',body:new FormData(event.currentTarget)});tell(result.message);}catch(error){tell(error.message,true);}});
document.querySelector('#mcp-credential-form')?.addEventListener('submit',async event=>{event.preventDefault();try{const result=await api(event.currentTarget.action,{method:'POST',body:new FormData(event.currentTarget)});document.querySelector('#mcp-credential-value').textContent=result.credential;document.querySelector('#mcp-credential-message').textContent=result.message;}catch(error){tell(error.message,true);}});
function revealSection() {
  const section = document.getElementById(location.hash.slice(1));
  if(!section)return;
  let revealed=false;
  for(let parent=section;parent;parent=parent.parentElement){
    if(parent.matches('details')&&!parent.open){parent.open=true;revealed=true;}
  }
  if(revealed)section.scrollIntoView({block:'start'});
}
revealSection();
addEventListener('hashchange', revealSection);
const settingsNav=document.querySelector('[data-settings-nav]');
if(settingsNav){
  const panes=[...document.querySelectorAll('[data-settings-pane]')];
  const links=[...settingsNav.querySelectorAll('a[href^="#"]')];
  document.documentElement.classList.add('settings-ready');
  function settingsURL(section){
    const url=new URL(location.href);url.searchParams.set('section',section);url.hash='';return url;
  }
  // A section is a tab, not a scroll anchor. Normalize incoming links before
  // the browser's final fragment scroll after document load.
  if(links.some(link=>link.hash===location.hash))history.replaceState(history.state,'',settingsURL(location.hash.slice(1)));
  function selectSettings(){
    const target=document.getElementById(location.hash.slice(1));
    let selected=target?.closest('[data-settings-pane]')?.dataset.settingsPane||new URL(location.href).searchParams.get('section');
    if(!selected){
      selected=settingsNav.dataset.default;
      if(!selected)try{selected=localStorage.getItem('campusboard.settings.section');}catch(_){}
    }
    if(!panes.some(p=>p.dataset.settingsPane===selected))selected='notifications';
    panes.forEach(p=>p.hidden=p.dataset.settingsPane!==selected);
    links.forEach(link=>{
      if(link.hash==='#'+selected)link.setAttribute('aria-current','page');
      else link.removeAttribute('aria-current');
    });
    const current=links.find(link=>link.hash==='#'+selected);
    if(current&&settingsNav.scrollWidth>settingsNav.clientWidth){
      const navBounds=settingsNav.getBoundingClientRect(),linkBounds=current.getBoundingClientRect();
      if(linkBounds.left<navBounds.left)settingsNav.scrollLeft+=linkBounds.left-navBounds.left;
      else if(linkBounds.right>navBounds.right)settingsNav.scrollLeft+=linkBounds.right-navBounds.right;
    }
    try{localStorage.setItem('campusboard.settings.section',selected);}catch(_){}
    // A tab selects a page section; keep its header visible instead of anchoring
    // the long form halfway down the viewport. Specific deep links still work.
    if(target && !links.some(link=>link.hash===location.hash))requestAnimationFrame(()=>target.scrollIntoView({block:'start'}));
    else window.scrollTo({top:0,left:0,behavior:'instant'});
  }
  selectSettings();
  addEventListener('hashchange',selectSettings);
  addEventListener('popstate',selectSettings);
  function navigateSettings(section){
    const url=settingsURL(section);
    if(url.href!==location.href)history.pushState(history.state,'',url);
    selectSettings();
  }
  links.forEach(link=>link.addEventListener('click',event=>{
    event.preventDefault();navigateSettings(link.hash.slice(1));
  }));
}
const notice = document.querySelector('#notice');
function tell(message, isError = false) {
  notice.textContent = message;
  notice.hidden = false;
  notice.classList.toggle('error', isError);
}
document.querySelector('#restore-upload')?.addEventListener('submit', async event => {
  event.preventDefault();
  const form = event.currentTarget, button = form.querySelector('button');
  const progress = document.querySelector('#restore-progress');
  const file = form.elements.backup.files[0];
  if (!file) return;
  button.disabled = true;
  progress.textContent = 'Archiv wird lokal geprüft. Je nach Größe kann das etwas dauern …';
  try {
    const result = await api('/settings/backups/check', {method:'POST', body:file, headers:{'Content-Type':'application/zip'}});
    progress.textContent = result.message;
    location.href = '/settings';
  } catch (error) {progress.textContent = error.message; button.disabled = false;}
});
document.querySelectorAll('[data-gotcha-form]').forEach(form => form.addEventListener('submit', async event => {
  event.preventDefault();
  const button=form.querySelector('button');button.disabled=true;
  try {
    const response=await fetch(form.action,{method:'POST',body:new FormData(form),headers:{'X-CSRF-Token':token,'X-Requested-With':'fetch'}});
    if(!response.ok)throw new Error((await response.json()).detail || 'Gotcha konnte nicht gespeichert werden.');
    history.replaceState(history.state,'','/model#gotchas');location.reload();
  } catch(error){tell(error.message,true);button.disabled=false;}
}));
document.querySelectorAll('.settings-form').forEach(form => form.addEventListener('submit', async event => {
  event.preventDefault();
  const button = form.querySelector('button'), progress = form.querySelector('.form-progress');
  button.disabled = true;
  if (progress) progress.textContent = 'Backup wird lokal erstellt. Bitte kurz warten …';
  try {
    const response = await fetch(form.action, {method:'POST', body:new FormData(form), headers:{'X-CSRF-Token':token, 'X-Requested-With':'fetch'}});
    if (!response.ok) throw new Error((await response.json()).detail || 'Anfrage fehlgeschlagen');
    if (response.headers.get('content-type')?.includes('application/json')) {
      const result = await response.json();
      if (progress) progress.textContent = 'Sicherung erstellt. Download startet; eine Kopie bleibt lokal unter backups.';
      location.href = result.download;
      button.disabled = false;
    } else location.href = '/settings';
  } catch (error) {tell(error.message, true); button.disabled = false; if (progress) progress.textContent = '';}
}));
async function refreshPageToken() {
  if (!tokenRefresh) tokenRefresh = (async () => {
    const response = await fetch('/api/session', {cache:'no-store', headers:{'X-Requested-With':'fetch'}});
    const session = await response.json();
    if (!response.ok || typeof session.csrf !== 'string' || !session.csrf) throw new Error('App-Verbindung erneuern fehlgeschlagen. Bitte erneut versuchen.');
    token = session.csrf;
    document.querySelector('meta[name="csrf-token"]').content = token;
    document.querySelectorAll('input[name="csrf"]').forEach(input => { input.value = token; });
  })();
  try { await tokenRefresh; } finally { tokenRefresh = null; }
}
async function api(url, options = {}) {
  const send = () => {
    const headers = new Headers(options.headers);
    headers.set('X-CSRF-Token', token);
    return fetch(url, {...options, headers});
  };
  let response = await send(), result = await response.json();
  // This explicit guard response proves the first request did not execute.
  // Never replay a timeout, an origin rejection or a failed application action.
  if (response.status === 403 && result.code === 'csrf_expired') {
    await refreshPageToken();
    response = await send(); result = await response.json();
  }
  if (!response.ok) throw new Error(result.detail || 'Anfrage fehlgeschlagen');
  return result;
}
document.querySelector('#sync-button').addEventListener('click', async () => {
  try {
    const result = await api('/api/sync', {method: 'POST'});
    tell(`Synchronisierung #${result.run_id} läuft im Hintergrund.`);
    await poll();
  } catch (error) {tell(error.message, true);}
});
document.querySelectorAll('[data-source-analysis]').forEach(button => button.addEventListener('click', async () => {
  try {
    const result = await api(`/api/sync?source_id=${button.dataset.sourceAnalysis}&analysis_only=true`, {method:'POST'});
    tell(`Gespeicherte Auswertung #${result.run_id} läuft im Hintergrund.`);
    await poll();
  } catch (error) {tell(error.message,true);}
}));
document.querySelector('#login-button').addEventListener('click', async event => {
  const button = event.currentTarget;
  button.disabled = true;
  tell('CampusBoard-Browser wird geöffnet …');
  try { const result = await api('/api/login', {method: 'POST'}); tell(result.message); }
  catch (error) {tell(error.message, true);}
  finally {button.disabled = false;}
});
document.querySelector('#chatgpt-connect')?.addEventListener('submit', async event => {
  event.preventDefault();
  const form = event.currentTarget, button = form.querySelector('button');
  button.disabled = true;
  try {
    const result = await api('/model/connect', {method: 'POST', body: new FormData(form)});
    tell(result.message);
    button.disabled = false;
  } catch (error) {tell(error.message, true); button.disabled = false;}
});
document.querySelector('#teachcenter-login-button')?.addEventListener('click', async event => {
  const button = event.currentTarget;
  button.disabled = true;
  try {const result = await api('/api/login?service=teachcenter', {method: 'POST'}); tell(result.message);}
  catch (error) {tell(error.message, true);}
  finally {button.disabled = false;}
});
document.querySelector('#browser-close-button')?.addEventListener('click', async event => {
  const button = event.currentTarget;
  button.disabled = true;
  try {
    await api('/api/browser/close', {method: 'POST'});
    tell('CampusBoard-TU-Browser geschlossen. Die gespeicherte Sitzung bleibt erhalten.');
    await poll();
  } catch (error) {tell(error.message, true);}
  finally {button.disabled = false;}
});
document.querySelectorAll('[data-note-form]').forEach(form=>{
  const day=form.querySelector('[data-note-date]'), clock=form.querySelector('[data-note-time]'), progress=form.querySelector('[data-note-progress]');
  const request=form.elements.request_id, button=form.querySelector('button');let submitting=false;
  const timeState=()=>{if(!day.value)clock.value='';clock.disabled=!day.value;};timeState();day.addEventListener('input',timeState);day.addEventListener('change',timeState);
  if(!request||!/^[a-f0-9]{32}$/i.test(request.value)){button.disabled=true;progress.textContent='Neue Eingabe nach App-Neustart verfügbar.';return;}
  form.addEventListener('input',()=>{if(!submitting)request.value=crypto.randomUUID().replaceAll('-','');});
  form.addEventListener('submit',async event=>{
    event.preventDefault();if(submitting)return;submitting=true;
    const body=new FormData(form), controls=[...form.elements], disabled=controls.map(control=>control.disabled);
    controls.forEach(control=>control.disabled=true);progress.textContent='Post-it wird gespeichert …';
    try{
      const result=await api(form.action,{method:'POST',body,headers:{'X-Requested-With':'fetch'}});
      if(!/^\/items\/[0-9]+$/.test(result.url||''))throw new Error('Speicherbestätigung fehlt. Dieselbe Eingabe erneut versuchen.');
      location.href=result.url;
    }catch(error){progress.textContent=error.message+' Deine Eingabe bleibt hier erhalten.';}
    finally{submitting=false;controls.forEach((control,index)=>control.disabled=disabled[index]);timeState();}
  });
});
document.querySelectorAll('.quick-form').forEach(form => form.addEventListener('submit', async event => {
  event.preventDefault();
  const button = form.querySelector('button');
  button.disabled = true;
  try {
    const result = await api(form.getAttribute('action'), {method: 'POST', body: new FormData(form), headers: {'X-Requested-With': 'fetch'}});
    if (form.elements.expected_revision && Number.isInteger(result.revision)) form.elements.expected_revision.value=String(result.revision);
    form.dispatchEvent(new CustomEvent('quick-form-result',{detail:{ok:true}}));
    // Paginated walls must refill from the complete filtered set after a change.
    if (form.hasAttribute('data-entry-action') || ['/board','/pins'].includes(location.pathname)) { location.reload(); return; }
    if (!form.elements.status) location.reload();
    else {
      const done=form.elements.status.value==='done';
      const remove=location.pathname==='/archive'?!done:done;
      const card=form.closest('[data-item-id]');
      if(remove)card?.remove();
      else if(location.pathname==='/board') {
        const statusFilter=new URLSearchParams(location.search).get('status');
        const group=card?.dataset.pinned==='true'?'pinned':form.elements.status.value;
        const target=statusFilter&&statusFilter!==form.elements.status.value?null:document.querySelector(`[data-work-status="${group}"] .postit-grid`);
        if(target&&card){target.querySelector('.empty')?.remove();if(card.parentElement!==target)target.append(card);}
        else if(card)card.remove();
      } else card?.classList.toggle('done',done);
      document.querySelectorAll('[data-work-status]').forEach(section=>{
        const count=section.querySelector('[data-work-count]');
        const length=section.querySelectorAll('[data-item-id]').length;
        if(count)count.textContent=String(length);
        const grid=section.querySelector('.postit-grid');
        if(grid&&!length&&!grid.querySelector('.empty')){const empty=document.createElement('p');empty.className='empty';empty.textContent='Keine weiteren passenden Einträge.';grid.append(empty);}
      });
      tell(done?'Erledigt. Der Eintrag bleibt im Archiv gespeichert.':'Bearbeitungsstatus gespeichert.');
    }
  } catch (error) {form.dispatchEvent(new CustomEvent('quick-form-result',{detail:{ok:false}}));tell(error.message, true);}
  finally {button.disabled = false;}
}));
let wasActive = false, polling = false, loginActive = false, lastPoll = 0;
let completionRevision = null, completionRefreshPending = false;
async function poll(force = true) {
  if (polling || document.hidden) return;
  if (!force && !wasActive && !loginActive && Date.now()-lastPoll<10000) return;
  lastPoll=Date.now();
  polling = true;
  try {
    const state = await api('/api/status');
    if(Number.isInteger(state.class_completion_revision)){
      if(completionRevision!==null&&state.class_completion_revision!==completionRevision)completionRefreshPending=true;
      completionRevision=state.class_completion_revision;
      if(completionRefreshPending&&['/board','/pins','/overview','/archive','/'].includes(location.pathname)
        &&!document.querySelector('dialog[open],.note-compose[open]')
        &&!document.activeElement?.matches('input,textarea,select,[contenteditable="true"]')){
        location.reload();return;
      }
    }
    const status = document.querySelector('#sync-state');
    status.textContent = state.active ? state.stage : state.login_mode ? 'Anmeldemodus aktiv · danach manuell synchronisieren' : 'Bereit';
    status.classList.toggle('working', state.active);
    status.closest('.sync-strip').hidden=!(state.active||state.login_mode);
    const meter=document.querySelector('#sync-progress'),label=document.querySelector('#sync-progress-label');
    meter.hidden=label.hidden=!state.active;
    if(state.active){
      const progress=state.progress;
      if(progress&&Number.isInteger(progress.sources_total)&&progress.sources_total>0){
        meter.max=progress.sources_total;meter.value=progress.sources_completed;
        label.textContent=`${progress.sources_completed}/${progress.sources_total} Quellen geprüft${progress.source?' · '+progress.source:''}`;
      }else{
        meter.removeAttribute('value');
        label.textContent=progress?.sources_total===0?'Keine aktive Quelle · Abschluss läuft':'Arbeitsumfang wird ermittelt …';
      }
    }
    if (wasActive && !state.active) {
      const activeSources = state.sources.filter(s => s.active && s.course_active !== 0);
      const remaining = activeSources.reduce((n,s) => n + Math.max(0, (s.analysis_pending ?? s.pending ?? 0) - (s.deferred_analysis || 0)), 0);
      const gaps = activeSources.reduce((n,s) => n + (s.reading_gaps || 0), 0);
      const access = activeSources.filter(s => ['failed','login_required','interrupted'].includes(s.state)).length;
      tell([state.run?.state === 'success' ? 'Quellen aktualisiert.' : 'Quellenabgleich teilweise abgeschlossen.',
        access ? `${access} ${access === 1 ? 'Quellenzugang' : 'Quellenzugänge'} prüfen.` : '', remaining ? `${remaining} ${remaining === 1 ? 'Auswertung' : 'Auswertungen'} offen.` : '',
        gaps ? 'Leselücken vorhanden.' : ''].filter(Boolean).join(' '));
      const link = document.createElement('a'); link.href = location.href; link.textContent = 'Ansicht aktualisieren →'; notice.append(link);
      if (access || remaining || gaps) { const details=document.createElement('a');details.href='/sources';details.textContent=' Quellenstatus ansehen →';notice.append(details); }
    }
    wasActive = state.active;loginActive=Boolean(state.login_mode);
  } catch (error) {document.querySelector('#sync-state').textContent = 'App nicht erreichbar - läuft CampusBoard noch?';document.querySelector('#sync-progress').hidden=true;document.querySelector('#sync-progress-label').hidden=true;document.querySelector('.sync-strip').hidden=false;}
  finally {polling = false;}
}
poll();
setInterval(()=>poll(false), 2500);
document.addEventListener('visibilitychange',()=>{if(!document.hidden)poll();});

document.querySelectorAll('[data-native-test]').forEach(button=>button.addEventListener('click',async()=>{button.disabled=true;try{const result=await api('/settings/notifications/test',{method:'POST'});tell(result.message);const label=document.querySelector('#voice-notification-state');if(label)label.textContent=result.message;}catch(error){tell(error.message,true);}finally{button.disabled=false;}}));

document.querySelectorAll('[data-open-voice]').forEach(button=>button.addEventListener('click',()=>document.querySelector('#voice-open')?.click()));

const sourceKind=document.querySelector('form[action="/sources"] select[name="kind"]');
if(sourceKind){
 const options=sourceKind.form.querySelector('[data-gitlab-options]');
 const update=()=>{if(!options)return;const enabled=sourceKind.value==='gitlab';options.hidden=!enabled;options.querySelectorAll('input').forEach(input=>input.disabled=!enabled);};
 sourceKind.addEventListener('change',update);update();
}

// Google is a local projection worker, never a source-sync trigger.
const googlePanel=document.querySelector('#google');
if(googlePanel){
  const status=document.querySelector('#google-status'), error=document.querySelector('#google-error');
  let googleConflictsKey;
  function showGoogle(value, resetConsent=false){
    if(resetConsent){for(const name of ['enabled','consent']){const input=googlePanel.parentElement.querySelector(`[data-google-form] [name="${name}"]`);if(input)input.checked=Boolean(value[name]);}}
    status.textContent=`${value.connected?'Verbunden':'Nicht verbunden'} · ${value.enabled?'Automatischer Abgleich aktiv':'Pausiert'}${value.account?' · '+value.account:''}${value.calendar_id?' · Ziel: StudyCenter':''}${value.last_success?' · zuletzt '+new Date(value.last_success).toLocaleString('de-AT'):''}`;
    error.textContent=value.error||'';
    googlePanel.querySelector('[data-google-action=connect]').textContent=value.connected?'Erneut anmelden':'Google verbinden';
    googlePanel.querySelector('[data-google-action=sync]').hidden=!value.connected;
    const conflictsKey=JSON.stringify(value.conflicts||[]);
    if(conflictsKey===googleConflictsKey)return;
    googleConflictsKey=conflictsKey;
    const conflicts=document.querySelector('#google-conflicts');conflicts.replaceChildren();
    for(const conflict of value.conflicts||[]){
      const row=document.createElement('div');row.className='card';
      const title=document.createElement('p');title.textContent=JSON.parse(conflict.payload).summary+' · '+conflict.error;row.append(title);
      for(const [keep,label] of [[false,'StudyCenter übernehmen'],[true,'Google behalten und nicht weiter spiegeln']]){
        const button=document.createElement('button');button.textContent=label;button.className='secondary';
        button.onclick=async()=>{button.disabled=true;const body=new FormData();body.set('identity',conflict.identity);body.set('role',conflict.role);body.set('keep',String(keep));
          try{await api('/settings/google/resolve',{method:'POST',body});await previewGoogle();}catch(e){tell(e.message,true);}finally{button.disabled=false;}};
        row.append(button);
      }conflicts.append(row);
    }
  }
  let googleStatusPending=false;
  async function refreshGoogleStatus(){
    if(document.hidden||googlePanel.closest('[data-settings-pane]')?.hidden||googleStatusPending)return;
    googleStatusPending=true;
    try{const value=await api('/api/desktop/status');showGoogle(value.google);}catch(_){tell('Google-Verbindungsstatus konnte nicht aktualisiert werden. Bitte Vorschau erneut laden.',true);}finally{googleStatusPending=false;}
  }
  addEventListener('focus',refreshGoogleStatus);
  document.addEventListener('visibilitychange',()=>{if(!document.hidden)refreshGoogleStatus();});
  async function previewGoogle(resetConsent=false){
    const result=await api('/settings/google/preview');showGoogle(result.status,resetConsent);
    const root=document.querySelector('#google-preview');root.closest('details')?.setAttribute('open','');root.replaceChildren();
    const heading=document.createElement('h3');heading.textContent=`Vorschau: ${result.selected.length} enthalten · ${result.skipped.length} ausgelassen`;root.append(heading);
    const search=document.createElement('input');search.placeholder='Vorschau nach Titel / Grund filtern';search.setAttribute('aria-label','Kalendervorschau filtern');root.append(search);
    const rows=document.createElement('div');root.append(rows);
    function render(){rows.replaceChildren();const q=search.value.toLocaleLowerCase('de');
      const matches=[...result.selected.map(e=>({...e,included:true})),...result.skipped].filter(e=>[e.title,e.course||'',e.reason||''].join(' ').toLocaleLowerCase('de').includes(q));
      for(const entry of matches.slice(0,80)){
        const line=document.createElement('div');line.className='google-preview-row';
        const link=document.createElement('a');link.href='/items/'+entry.id;link.textContent=entry.title;line.append(link);
        const detail=document.createElement('small');detail.textContent=(entry.course||'')+' · '+(entry.included?`${entry.payload.start.dateTime||entry.payload.start.date}${entry.marker?' · 1-Minuten-Darstellung':''}${entry.optional?' · optionaler Fristmarker':''}`:entry.reason);line.append(detail);
        const button=document.createElement('button');button.className='secondary small';button.textContent=entry.included?'Abwählen':'Auswählen';
        button.onclick=async()=>{const body=new FormData();body.set('item_id',entry.id);body.set('include',String(!entry.included));button.disabled=true;
          try{await api('/settings/google/selection',{method:'POST',body});await previewGoogle();}catch(e){tell(e.message,true);button.disabled=false;}};
        // Selection never overrides conflicts, booking or missing-time guards.
        line.append(button);rows.append(line);
      }
      if(matches.length>80){const p=document.createElement('p');p.textContent='80 Treffer angezeigt – mit dem Suchfeld eingrenzen.';rows.append(p);}
    }
    search.addEventListener('input',render);render();
  }
  document.querySelector('#google-preview-button').onclick=()=>previewGoogle().catch(e=>tell(e.message,true));
  document.querySelectorAll('[data-google-form]').forEach(form=>form.addEventListener('submit',async e=>{
    e.preventDefault();const button=form.querySelector('button');button.disabled=true;
    try{const value=await api(form.action,{method:'POST',body:new FormData(form)});tell(value.message);await previewGoogle(true);}catch(e){tell(e.message,true);}finally{button.disabled=false;}
  }));
  document.querySelectorAll('[data-google-action]').forEach(button=>button.onclick=async()=>{
    button.disabled=true;try{const value=await api('/settings/google/'+button.dataset.googleAction,{method:'POST'});if(value.message)tell(value.message);await previewGoogle(['connect','disconnect'].includes(button.dataset.googleAction));}catch(e){tell(e.message,true);}finally{button.disabled=false;}
  });
}
document.querySelector('#desktop-install')?.addEventListener('click',async()=>{try{const value=await api('/settings/desktop/install',{method:'POST'});tell(value.message);}catch(e){tell(e.message,true);}});
document.querySelector('#session-probe')?.addEventListener('click',async e=>{
  e.target.disabled=true;try{const result=await api('/api/sessions/probe',{method:'POST'});const root=document.querySelector('#session-results');root.replaceChildren();
    const labels={authenticated:'Angemeldet',login_required:'Anmeldung nötig',unreachable:'Nicht erreichbar',busy:'Belegt',unknown:'Unbekannt'};
    for(const [key,value] of Object.entries(result)){const p=document.createElement('p');p.textContent=key+': '+(labels[value.state]||value.state)+' · '+value.reason;root.append(p);}
  }catch(e){tell(e.message,true);}finally{e.target.disabled=false;}
});

const autostartForm=document.querySelector('#autostart-form');
document.querySelector('[data-luna-mode]')?.addEventListener('submit',async event=>{
  event.preventDefault();const form=event.currentTarget,buttons=[...form.querySelectorAll('button[name="mode"]')],status=form.querySelector('[data-luna-mode-status]'),body=new FormData(form);body.set('mode',event.submitter.value);buttons.forEach(b=>b.disabled=true);
  try{const value=await api(form.action,{method:'POST',body});buttons.forEach(b=>{const active=b.value===value.mode;b.setAttribute('aria-pressed',String(active));b.classList.toggle('secondary',!active);});status.textContent=value.message;}
  catch(error){status.textContent=error.message;}finally{buttons.forEach(b=>b.disabled=false);}
});
if(autostartForm)autostartForm.addEventListener('submit',async event=>{
  event.preventDefault();const button=autostartForm.querySelector('button'),result=document.querySelector('#autostart-result');button.disabled=true;result.textContent='Wird gespeichert …';
  try{const value=await api(autostartForm.action,{method:'POST',body:new FormData(autostartForm),headers:{'X-Requested-With':'fetch'}});autostartForm.elements.enabled.checked=value.enabled;result.textContent=value.message;result.classList.remove('error');}
  catch(error){result.textContent=error.message;result.classList.add('error');}
  finally{button.disabled=false;}
});
