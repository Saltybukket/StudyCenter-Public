(() => {
 'use strict';
 const csrf=document.querySelector('meta[name="csrf-token"]').content;
 const notice=document.getElementById('notice');
 const report=(text,error=false)=>{notice.textContent=text;notice.hidden=false;notice.classList.toggle('error',error);notice.scrollIntoView({block:'nearest'});};
 async function api(url,data,method='POST'){
  const options={method,headers:{'X-CSRF-Token':csrf}};
  if(data instanceof FormData)options.body=data;
  else if(data!==undefined){options.headers['Content-Type']='application/json';options.body=JSON.stringify(data);}
  const response=await fetch(url,options);
  if(!response.ok){let problem;try{problem=await response.json();}catch{problem={error:'Vorgang fehlgeschlagen'};}throw Error(problem.error||problem.detail||'Vorgang fehlgeschlagen');}
  const type=response.headers.get('content-type')||'';
  return type.includes('json')?response.json():response.blob();
 }
 const bind=(selector,event,fn)=>document.querySelectorAll(selector).forEach(el=>el.addEventListener(event,async e=>{try{await fn(e,el);}catch(error){report(error.message,true);}}));
 const fields=form=>Object.fromEntries(new FormData(form));
 let pending=null,lastReceipt=null;
 const actionText=actions=>actions.map(a=>{
  const names={create_note:'Neue persönliche Notiz',create_task:'Neue persönliche Aufgabe',create_event:'Neuer persönlicher Termin',update:'Persönliche Angabe ändern',set_override:'Persönliche Angabe ändern',set_status:'Persönlicher Status',set_reminder:'Persönliche Erinnerung',decide_source:'Persönliche Quellenentscheidung',delete:'Persönlichen Eintrag entfernen'};
  const labels={title:'Titel',body:'Text',date:'Datum',time:'Uhrzeit',end_date:'Schlussdatum',end_time:'Schlusszeit',pinned:'Pin',obligation:'Pflichtgrad',date_meaning:'Datumsrolle'};
  const lines=[names[a.type]||'Persönliche Aktion'];
  if(a.title)lines.push(a.title);
  if(a.status)lines.push('Status: '+a.status);
  if(a.type==='set_reminder')lines.push((a.enabled?'Aktiv':'Aus')+' · '+a.offsets.join(', ')+' Minuten davor');
  for(const [key,value]of Object.entries(a.fields||a))if(labels[key]&&key!=='title')lines.push(labels[key]+': '+(value===null?'Keine Angabe':value));
  if(a.type==='decide_source')lines.push((labels[a.field]||a.field)+': '+a.value+'\nGrund: '+a.reason);
  return lines.join('\n');
 }).join('\n\n');
 const previewDialog=document.getElementById('preview-dialog'),receiptDialog=document.getElementById('receipt-dialog');
 async function preview(actions,versions){
  const result=await api('/api/actions/preview',{actions});
  pending={actions:result.actions,expected_versions:versions||result.versions,request_id:crypto.randomUUID()};
  document.getElementById('preview-content').textContent=actionText(result.actions);
  previewDialog.showModal();
 }
 function showReceipt(receipt){
  lastReceipt=receipt;document.getElementById('receipt-content').textContent=`${receipt.count||receipt.item_ids?.length||1} Änderung(en) übernommen.`;
  receiptDialog.showModal();
 }
 bind('#preview-apply','click',async()=>{if(!pending)return;const result=pending.proposal_id?await api('/api/assistant/apply',{proposal_id:pending.proposal_id}):await api('/api/actions',pending);previewDialog.close();pending=null;showReceipt(result);});
 bind('#preview-cancel','click',()=>{pending=null;previewDialog.close();});
 bind('#receipt-undo','click',async()=>{if(!lastReceipt)return;const result=await api('/api/actions/undo',{request_id:lastReceipt.request_id,undo_id:crypto.randomUUID()});if(result.state==='blocked'||result.blocked){report('Rücknahme blockiert: Der Eintrag wurde danach geändert. Es wurde nichts überschrieben.',true);receiptDialog.close();return;}location.reload();});
 bind('#receipt-close','click',()=>location.reload());
 bind('#create-form','submit',async(e,form)=>{e.preventDefault();const data=fields(form);const kind=data.kind;delete data.kind;for(const key of ['date','time'])if(!data[key])delete data[key];if(data.time&&!data.date)throw Error('Eine Uhrzeit braucht ein Datum.');await preview([{type:'create_'+kind,...data}]);});
 bind('#edit-form','submit',async(e,form)=>{e.preventDefault();const data=fields(form);data.date=data.date||null;data.time=data.time||null;await preview([{type:'update',item_id:+form.dataset.id,fields:data}],{[form.dataset.id]:+form.dataset.version});});
 bind('.item-status','click',async(e,b)=>preview([{type:'set_status',item_id:+b.dataset.id,status:b.dataset.status}],{[b.dataset.id]:+b.dataset.version}));
 bind('#detail-status','change',async(e,b)=>preview([{type:'set_status',item_id:+b.dataset.id,status:b.value}],{[b.dataset.id]:+b.dataset.version}));
 bind('.item-pin','click',async(e,b)=>preview([{type:'update',item_id:+b.dataset.id,fields:{pinned:b.dataset.pinned==='true'}}],{[b.dataset.id]:+b.dataset.version}));
 bind('.decision-form','submit',async(e,form)=>{e.preventDefault();const data=fields(form);await preview([{type:'decide_source',item_id:+form.dataset.id,document_id:+form.dataset.document,...data}],{[form.dataset.id]:+form.dataset.version});});
 bind('#import-form','submit',async(e,form)=>{e.preventDefault();const doc=await api('/api/documents/import',new FormData(form));location.href='/documents/'+doc.id;});
 bind('#extract','click',async(e,b)=>{await api('/api/documents/'+b.dataset.id+'/extract',{});report('KI-Auswertung gestartet. Alle Seiten werden verarbeitet; Fehler bleiben sichtbar.');});
 bind('#reread','click',async(e,b)=>{const doc=await api(`/api/documents/${b.dataset.id}/reread/${b.dataset.page}`,{ignore_grid:document.getElementById('ignore-grid').checked});location.href=`/documents/${doc.id}?page=${b.dataset.page}`;});
 bind('#source-form','submit',async(e,form)=>{e.preventDefault();await api('/api/sources',fields(form));location.reload();});
 bind('.saved-course-selection','submit',async(e,form)=>{e.preventDefault();await api(`/api/sources/${form.dataset.id}/select`,{courses:[...form.querySelectorAll('input:checked')].map(input=>input.value)});report('Kursauswahl gespeichert.');});
 bind('.source-login','click',async(e,b)=>{report('Eigenes TU-Anmeldefenster wird geöffnet.');await api(`/api/sources/${b.dataset.id}/login`,{});});
 bind('.source-discover','click',async(e,b)=>{await api(`/api/sources/${b.dataset.id}/discover`,{});report('Eigene Kurse werden gelesen.');});
 bind('.source-sync','click',async(e,b)=>{await api(`/api/sources/${b.dataset.id}/sync`,{});report('Manueller Quellenabgleich gestartet.');});
 bind('.source-resume','click',async(e,b)=>{await api(`/api/sources/${b.dataset.id}/sync`,{resume:true});report('Quellenabgleich fortgesetzt.');});
 bind('.source-cancel','click',async(e,b)=>{await api(`/api/sources/${b.dataset.id}/cancel`,{});report('Abbruch angefordert; abgeschlossene Originale bleiben erhalten.');});
 bind('#browser-close','click',async()=>{await api('/api/browser/close',{});report('Eigener TU-Browser geschlossen.');});
 bind('.exclusion-form','submit',async(e,form)=>{e.preventDefault();const current=await api('/api/sources',undefined,'GET');const source=current.find(s=>s.id===+form.dataset.id);const options={...(source.options||{}),excluded_urls:fields(form).excluded_urls.split('\n').map(s=>s.trim()).filter(Boolean)};await api('/api/sources',{source_id:source.id,name:source.name,url:source.url,kind:source.kind,options});report('Ausschlüsse gelten für Abruf und KI-Analyse.');});
 let sessionId=null,lastAnswer='',recorder=null,recordChunks=[];
 function addTurn(text,role){const div=document.createElement('div');div.className='conversation-turn '+role;div.textContent=text;document.getElementById('conversation').append(div);return div;}
 async function ask(text,options={}){
  addTurn(text,'user');
  const result=await api('/api/assistant',{text,session_id:sessionId,...options});sessionId=result.session_id;lastAnswer=result.answer;
  const turn=addTurn(result.answer+(result.question?'\n\n'+result.question:''),'assistant');
  document.getElementById('assistant-provider').textContent=result.provider+' · '+result.model;
  if(result.choices?.length){const group=document.createElement('div');group.className='choice-buttons';for(const choice of result.choices){const b=document.createElement('button');b.className='secondary';b.textContent=choice;b.addEventListener('click',()=>ask(choice,options).catch(e=>report(e.message,true)));group.append(b);}turn.append(group);}
  for(const link of result.links||[]){const a=document.createElement('a');a.href=link.path;a.textContent='Öffnen: '+link.title;turn.append(a,document.createElement('br'));}
  for(const citation of result.citations||[]){const a=document.createElement('a');a.href=`/documents/${citation.document_id}?page=${citation.page}`;a.textContent=`\nBeleg · Seite ${citation.page}: ${citation.quote}`;turn.append(a);}
  if(result.receipt)showReceipt(result.receipt);
  else if(result.preview){const b=document.createElement('button');b.textContent='Vorschau prüfen';b.addEventListener('click',()=>{pending={proposal_id:result.proposal_id};document.getElementById('preview-content').textContent=actionText(result.preview.actions);previewDialog.showModal();});turn.append(b);}
 }
 bind('#assistant-form','submit',async(e,form)=>{e.preventDefault();const text=form.elements.text.value;const docIds=[...form.elements.documents.selectedOptions].map(o=>+o.value);form.querySelector('button').disabled=true;try{await ask(text,{task:form.elements.questions.checked?'questions':'assistant',document_ids:docIds});form.elements.text.value='';}finally{form.querySelector('button').disabled=false;}});
 bind('#provider-kind','change',(e,select)=>{const input=document.querySelector('#provider-form [name=base_url]');input.value=select.value==='ollama'?'http://localhost:11434':select.value==='lmstudio'?'http://localhost:1234/v1':'';});
 bind('#provider-form','submit',async(e,form)=>{e.preventDefault();const data=fields(form);for(const key of ['session_only','allow_lan','content_consent'])data[key]=form.elements[key].checked;if(data.provider==='chatgpt'){if(!data.oauth_registration)throw Error('Eigenes ChatGPT-Konto auswählen; nach der Anmeldung diese Seite neu öffnen.');data.profile_id=data.oauth_registration;}delete data.oauth_registration;try{await api('/api/providers',data);report('KI-Profil gespeichert. Gewählte Inhalte gehen nur bei deiner ausdrücklichen KI-Aktion an diesen Anbieter.');}finally{form.elements.api_key.value='';}});
 bind('#provider-probe','click',async()=>{const result=await api('/api/providers/probe',{});report('Synthetischer Verbindungstest: '+JSON.stringify(result));});
 bind('.provider-select','click',async(e,b)=>{await api('/api/providers/select',{profile_id:b.dataset.id});report('Profil gewählt; Inhaltsübertragung für dieses Profil bitte erneut bestätigen.');});
 bind('.provider-disconnect','click',async(e,b)=>{await api('/api/providers/disconnect',{profile_id:b.dataset.id});location.reload();});
 bind('#oauth-start','click',async()=>{const result=await api('/api/oauth/start',{session_only:document.querySelector('#provider-form [name=session_only]').checked});report(result.status);});
 bind('#mail-form','submit',async(e,form)=>{e.preventDefault();try{await api('/api/mail/configure',fields(form));report('Eigener Mailzugang gespeichert. Unter Kurse & Quellen eine IMAP-Quelle hinzufügen und manuell synchronisieren.');}finally{form.elements.password.value='';}});
 bind('#theme','change',(e,select)=>{window.scTheme(select.value);return api('/api/settings',{theme:select.value});});
 bind('#compact','change',(e,input)=>{document.body.classList.toggle('compact',input.checked);return api('/api/settings',{compact:input.checked});});
 document.body.classList.toggle('compact',document.getElementById('compact').checked);
 for(const [selector,key] of [['#direct-mode','direct_mode'],['#background','background']])bind(selector,'change',(e,input)=>api('/api/settings',{[key]:input.checked}));
 bind('#setup-finish','click',async()=>{await api('/api/settings',{onboarding_complete:true,onboarding_step:5});location.href='/overview';});
 const minutes=s=>s.split(',').map(x=>x.trim()).filter(Boolean).map(Number);
 bind('#reminder-settings','submit',async(e,form)=>{e.preventDefault();const data=fields(form);const offsets={};for(const key of ['exam','exercise','lecture','task','registration'])offsets[key]=minutes(data[key]);await api('/api/reminders/settings',{enabled:form.elements.enabled.checked,offsets,quiet_start:data.quiet_start,quiet_end:data.quiet_end});report('Erinnerungen gespeichert.');});
 bind('#personal-reminder','submit',async(e,form)=>{e.preventDefault();await preview([{type:'set_reminder',item_id:+form.dataset.id,enabled:form.elements.enabled.checked,offsets:minutes(form.elements.minutes.value)}]);});
 bind('.notification-read','click',async(e,b)=>{await api('/api/notifications/read',{job_key:b.dataset.id});location.reload();});
 async function saveMcp(rotate=false){const data=await api('/api/mcp/configure',{enabled:document.getElementById('mcp-enable').checked,write:document.getElementById('mcp-write').checked,rotate});document.getElementById('mcp-result').textContent=data.new_token?`Endpunkt: ${data.endpoint}\nNeuer Zugang (nur hier sichtbar): ${data.new_token}`:`${data.enabled?'Aktiv':'Deaktiviert'} · ${data.endpoint}`;}
 bind('#mcp-enable','change',()=>saveMcp());bind('#mcp-write','change',()=>saveMcp());bind('#mcp-token','click',()=>saveMcp(true));
 bind('#backup','click',async()=>{const blob=await api('/api/backup',{});const a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download='studycenter-public-backup.zip';a.click();setTimeout(()=>URL.revokeObjectURL(a.href),1000);report('Private Sicherung erstellt. Nicht öffentlich teilen.');});
 bind('#restore-form','submit',async(e,form)=>{e.preventDefault();const data=await api('/api/backup/preview',new FormData(form));const root=document.getElementById('restore-preview');root.replaceChildren();const p=document.createElement('p');p.textContent='Sicherung geprüft. Die Wiederherstellung ersetzt deinen Public-Bestand; vorher wird eine Rückfallsicherung erstellt.';root.append(p);const pre=document.createElement('pre');pre.textContent=JSON.stringify(data.preview.counts||data.preview,null,2);root.append(pre);const b=document.createElement('button');b.textContent='Bewusst wiederherstellen';b.addEventListener('click',async()=>{try{await api('/api/backup/restore',{restore_id:data.restore_id,confirmed:true});location.reload();}catch(error){report(error.message,true);}});root.append(b);});
 bind('#speech-settings','submit',async(e,form)=>{e.preventDefault();const enabled=form.elements.enabled.checked;await api('/api/speech/configure',{enabled,model:form.elements.model.value});if(enabled&&form.elements.allow_download.checked){report('Das gewählte lokale Sprachmodell wird geprüft heruntergeladen.');await api('/api/speech/download',{allow_download:true});}report('Spracheinstellung gespeichert.');});
 bind('#speak','click',()=>{if(!('speechSynthesis'in window))throw Error('Dieser Browser bietet kein Vorlesen an.');speechSynthesis.cancel();const utterance=new SpeechSynthesisUtterance(lastAnswer||'Noch keine Antwort zum Vorlesen.');utterance.lang='de-DE';speechSynthesis.speak(utterance);});
 bind('#speech-stop','click',()=>{if('speechSynthesis'in window)speechSynthesis.cancel();});
 bind('#record','click',async(e,b)=>{if(recorder&&recorder.state==='recording'){recorder.stop();b.textContent='Sprache aufnehmen';return;}if(!navigator.mediaDevices?.getUserMedia||!window.MediaRecorder)throw Error('Mikrofonaufnahme ist in diesem Browser nicht verfügbar.');const status=await api('/api/speech/status',undefined,'GET');if(!status.enabled||!status.model_ready||!status.runtime_installed)throw Error('Sprache zuerst unter Einstellungen einrichten und Modell herunterladen.');const stream=await navigator.mediaDevices.getUserMedia({audio:true});recordChunks=[];recorder=new MediaRecorder(stream);recorder.ondataavailable=event=>recordChunks.push(event.data);recorder.onstop=async()=>{stream.getTracks().forEach(t=>t.stop());try{const form=new FormData();form.append('file',new Blob(recordChunks,{type:recorder.mimeType}),'aufnahme.webm');const result=await api('/api/speech/transcribe',form);document.querySelector('#assistant-form [name=text]').value=result.text;report('Erkannte Sprache eingefügt. Vor dem Senden prüfen.');}catch(error){report(error.message,true);}};recorder.start();b.textContent='Aufnahme beenden';setTimeout(()=>{if(recorder.state==='recording'){recorder.stop();b.textContent='Sprache aufnehmen';}},60000);});
 bind('#components-install','click',async()=>{const result=await api('/api/components/install',{});document.getElementById('component-status').textContent=result.status||'Komponentenprüfung gestartet.';});
 bind('#remove-data','submit',async(e,form)=>{e.preventDefault();await api('/api/data/remove',{confirmed:true,confirmation:form.elements.confirmation.value});report('Diese Public-Instanz beendet sich und entfernt ihren eigenen Bestand.');});
 bind('#shutdown','click',async()=>{await api('/api/shutdown',{});report('Diese Public-Instanz beendet sich.');});
 const seen=new Set();
 async function poll(){
  try{const jobs=await api('/api/jobs',undefined,'GET');const root=document.getElementById('jobs');for(const [name,job]of Object.entries(jobs)){
   let line=document.getElementById('job-'+name.replaceAll('/','-'));if(!line){line=document.createElement('p');line.id='job-'+name.replaceAll('/','-');root.append(line);}line.textContent=`${name.startsWith('discover')?'Kurserkennung':name.startsWith('sync')?'Quellenabgleich':name.startsWith('oauth')?'KI-Anmeldung':'Verarbeitung'}: ${job.state}${job.error?' · '+job.error:''}`;
   if(job.state==='complete'&&name.startsWith('discover/')&&!seen.has(name+JSON.stringify(job.result))){seen.add(name+JSON.stringify(job.result));const id=name.split('/')[1];const target=document.getElementById('courses-'+id);if(target){target.replaceChildren();const form=document.createElement('form');form.className='course-selection';for(const course of job.result){const label=document.createElement('label');label.className='check';const input=document.createElement('input');input.type='checkbox';input.value=course.external_id;label.append(input,document.createTextNode(course.title||course.name||course.external_id));form.append(label);}const b=document.createElement('button');b.textContent='Ausgewählte Kurse speichern';form.append(b);form.addEventListener('submit',async e=>{e.preventDefault();try{await api(`/api/sources/${id}/select`,{courses:[...form.querySelectorAll('input:checked')].map(i=>i.value)});report('Eigene Kursauswahl gespeichert. Jetzt synchronisieren ist bereit.');}catch(error){report(error.message,true);}});target.append(form);}}
  }for(const section of document.querySelectorAll('[data-source]')){const progress=await api(`/api/sources/${section.dataset.source}/status`,undefined,'GET');document.getElementById('source-progress-'+section.dataset.source).textContent=`${progress.state}: ${progress.fetched||0} gelesen, ${progress.imported||0} gespeichert${progress.complete?' · vollständig':' · Vollständigkeit noch nicht bestätigt'}${progress.gaps?.length?'\nLeselücken: '+progress.gaps.map(g=>typeof g==='string'?g:g.reason||g.error||'Quelle nicht vollständig gelesen').join('; '):''}`;}
  }catch{/* Server can have been explicitly stopped. */}
 }
 poll();setInterval(poll,3000);
})();
