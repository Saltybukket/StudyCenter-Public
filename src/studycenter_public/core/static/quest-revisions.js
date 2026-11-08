(() => {
  const dialog=document.querySelector('#quest-revision-dialog');if(!dialog)return;
  const body=document.querySelector('#quest-revision-body'),status=document.querySelector('#quest-revision-status'),actions=document.querySelector('#quest-revision-actions'),title=document.querySelector('#quest-revision-title');
  let quest=null,revision=null,busy=false,mutating=false,epoch=0;
  const el=(tag,cls,text)=>{const n=document.createElement(tag);n.className=cls||'';if(text!==undefined)n.textContent=text;return n;};
  const button=(text,fn,secondary=false)=>{const b=el('button',secondary?'secondary':'',text);b.type='button';b.addEventListener('click',fn);return b;};
  const post=(action,data)=>api(`/api/quests/${encodeURIComponent(quest.id)}/${action}`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});
  const dateLabel=value=>value?value.split('-').reverse().join('.'):'Ohne Tag';
  function lock(value){busy=value;dialog.setAttribute('aria-busy',String(value));dialog.querySelectorAll('#quest-revision-body input,#quest-revision-actions button').forEach(n=>n.disabled=value);}
  function render(){
    body.replaceChildren();actions.replaceChildren();status.textContent=revision.kind==='schedule'?revision.summary:`${revision.changes.length} ${revision.changes.length===1?'Anpassung':'Anpassungen'} für deinen Lernweg vorgeschlagen.`;
    if(revision.kind==='source'){const explanation=el('details','quest-revision-explanation');explanation.append(el('summary','','Warum diese Vorschläge?'),el('p','',revision.summary));body.append(explanation);}
    if(revision.warnings?.length){const warnings=el('details','quest-revision-explanation quest-warning');warnings.append(el('summary','',`${revision.warnings.length} ${revision.warnings.length===1?'Hinweis':'Hinweise'} beachten`));revision.warnings.forEach(w=>warnings.append(el('p','',w)));body.append(warnings);}
    body.append(el('p','quest-caption','Erledigte Schritte und eigene Texte bleiben erhalten. Wähle die Anpassungen, die du übernehmen möchtest.'));
    revision.changes.forEach(change=>{
      const row=el('section','quest-revision-change'),label=el('label','quest-revision-choice'),check=el('input');check.type='checkbox';check.checked=!change.requires_choice;check.value=change.id;check.name='revision-choice';label.append(check,el('strong','',change.title));row.append(label,el('p','quest-revision-reason',change.reason));
      if(revision.kind==='schedule'){row.append(el('p','quest-revision-date',`${dateLabel(change.before)} → ${dateLabel(change.after)}`));}
      else {
        if(!change.before){row.append(el('span','quest-revision-new','Neuer Schritt'));if(change.position_label)row.append(el('p','quest-caption',`Im Lernweg vor „${change.position_label}“`));if(change.after.planned_date)row.append(el('p','quest-revision-date',`Vorgeschlagen: ${dateLabel(change.after.planned_date)}`));}
        ['title','topic','detail','minutes'].forEach(field=>{if(!change.before&&field==='title'||change.before&&change.before[field]===change.after[field])return;const names={title:'Schritt',topic:'Thema',detail:'Aufgabe',minutes:'Minuten'},diff=el('div','quest-revision-diff');diff.append(el('strong','',names[field]));if(change.before)diff.append(el('p','quest-revision-before',String(change.before[field]||'—')));diff.append(el('p','quest-revision-after',String(change.after[field]||'—')));row.append(diff);});
        if(change.after.materials?.length){const links=el('div','quest-step-materials');change.after.materials.forEach(ref=>{const a=el('a','quest-link',`${ref.title} · S. ${ref.page}`);a.href=ref.original_only?`/documents/${ref.document_id}/pages/${ref.page}.png`:`/documents/${ref.document_id}?page=${ref.page}&reading=${encodeURIComponent(ref.reading_id||'')}`;a.target='_blank';a.rel='noopener noreferrer';links.append(a);});row.append(links);}else if(change.before?.materials?.length)row.append(el('p','quest-caption','Bisherige Materialverweise werden entfernt, weil keine passende aktuelle Stelle vorliegt.'));
      }
      body.append(row);
    });
    if(!revision.changes.length)body.append(el('p','quest-empty',revision.kind==='schedule'?'Die offenen Schritte sind bereits passend verteilt.':'Keine fachliche Anpassung vorgeschlagen. Dein Plan kann so bleiben.'));
    if(revision.changes.length||revision.kind==='source')actions.append(button(revision.changes.length?'Auswahl übernehmen':'Prüfung abschließen',apply));
    actions.append(button('Plan behalten',dismiss,true));
  }
  async function apply(){
    if(busy||!revision)return;const selected=[...body.querySelectorAll('input[name=revision-choice]:checked')].map(n=>n.value);mutating=true;lock(true);status.textContent='Speichere deine Auswahl …';
    try{const result=await post('apply-revision',{version:revision.version,revision_id:revision.id,selected_ids:selected});quest=result.quest;body.replaceChildren(el('p','quest-empty','Dein Plan ist aktualisiert. Dein Fortschritt bleibt erhalten.'));actions.replaceChildren(button('Fertig',()=>dialog.close()),button('Rückgängig',undo,true));status.textContent='Anpassung gespeichert.';document.dispatchEvent(new CustomEvent('quest:updated'));}
    catch(e){status.textContent=`Speichern nicht bestätigt: ${e.message}`;}finally{mutating=false;lock(false);}
  }
  async function dismiss(){if(busy)return;mutating=true;lock(true);try{await post('dismiss-revision',{version:revision.version,revision_id:revision.id});document.dispatchEvent(new CustomEvent('quest:updated'));dialog.close();}catch(e){status.textContent=e.message;}finally{mutating=false;lock(false);}}
  async function undo(){if(busy)return;mutating=true;lock(true);try{await post('undo-revision',{version:quest.version,revision_id:revision.id});status.textContent='Der vorherige Plan ist wiederhergestellt.';body.replaceChildren();actions.replaceChildren(button('Schließen',()=>dialog.close()));document.dispatchEvent(new CustomEvent('quest:updated'));}catch(e){status.textContent=e.message;}finally{mutating=false;lock(false);}}
  document.addEventListener('quest:revision',async event=>{
    if(busy)return;quest=event.detail.quest;revision=null;const action=event.detail.action,token=++epoch;
    if(action==='confirm-sources'){
      title.textContent='Anpassungen aus deinen Unterlagen';body.replaceChildren(el('p','quest-caption','Verknüpfte Quellen haben sich geändert. Studi kann die aktuellen gespeicherten Unterlagen mit deinem Lernweg vergleichen. Dein Plan bleibt zunächst unverändert.'));actions.replaceChildren(button('Mit Studi prüfen',()=>document.dispatchEvent(new CustomEvent('quest:revision',{detail:{quest,action:'suggest'}}))),button('Schließen',()=>dialog.close(),true));status.textContent='';if(!dialog.open)dialog.showModal();return;
    }
    title.textContent=action==='replan'?'Ab heute weiterplanen':'Anpassungen aus deinen Unterlagen';body.replaceChildren();actions.replaceChildren();status.textContent=action==='replan'?'Verteile offene Schritte mit Blick auf Fristen und deine anderen Quests …':'Studi prüft aktuelle gespeicherte Unterlagen. Das kann bis zu zwei Minuten dauern …';if(!dialog.open)dialog.showModal();lock(true);
    try{const result=action==='resume-revision'?await api(`/api/quests/${encodeURIComponent(quest.id)}/revision`):await post(action,{version:quest.version});document.dispatchEvent(new CustomEvent('quest:updated'));if(token!==epoch)return;revision=result.revision;title.textContent=revision.kind==='schedule'?'Ab heute weiterplanen':'Anpassungen aus deinen Unterlagen';render();}catch(e){if(token===epoch){status.textContent=`Der Plan bleibt unverändert: ${e.message}`;actions.append(button('Schließen',()=>dialog.close(),true));}}finally{if(token===epoch)lock(false);}
  });
  dialog.addEventListener('close',()=>{epoch++;busy=false;});
  dialog.addEventListener('cancel',event=>{if(mutating)event.preventDefault();});
  dialog.addEventListener('click',event=>{if(mutating&&event.target.closest('[data-close-dialog]')){event.preventDefault();event.stopImmediatePropagation();}},true);
})();
