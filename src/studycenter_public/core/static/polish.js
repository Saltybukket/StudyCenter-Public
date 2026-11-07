(() => {
  const toggle=document.getElementById('mobile-search-toggle'),search=document.getElementById('header-search-form');
  if(!toggle||!search)return;
  document.body.classList.add('polish-ready');
  function setSearch(open){document.body.classList.toggle('global-search-open',open);toggle.setAttribute('aria-expanded',String(open));toggle.setAttribute('aria-label',open?'Suche schließen':'Suche öffnen');}
  toggle.addEventListener('click',()=>{const open=toggle.getAttribute('aria-expanded')!=='true';setSearch(open);if(open)search.querySelector('input').focus();});
  search.addEventListener('keydown',event=>{if(event.key==='Escape'&&matchMedia('(max-width:700px)').matches){event.preventDefault();setSearch(false);toggle.focus();}});
  matchMedia('(max-width:700px)').addEventListener('change',()=>setSearch(false));
})();
