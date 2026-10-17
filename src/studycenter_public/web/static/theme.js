// Only Public appearance preferences; unavailable browser storage is harmless.
(() => {
  const modes=new Set(['light','dark','system']);
  const system=matchMedia('(prefers-color-scheme: dark)');
  let mode='system';
  try {const saved=localStorage.getItem('scpublic-theme');if(modes.has(saved))mode=saved;} catch (_) {}
  function apply(){
    document.documentElement.dataset.theme=mode==='system'?(system.matches?'dark':'light'):mode;
    document.querySelectorAll('[data-theme-choice]').forEach(input=>{input.checked=input.value===mode;});
    const select=document.getElementById('theme');if(select)select.value=mode;
  }
  window.scTheme=value=>{if(!modes.has(value))return;mode=value;try{localStorage.setItem('scpublic-theme',value);}catch(_){}apply();};
  window.scThemeMode=()=>mode;
  apply();system.addEventListener('change',apply);
  document.addEventListener('DOMContentLoaded',()=>{
    const saved=document.body.dataset.preferredTheme;
    if(modes.has(saved))window.scTheme(saved);else apply();
  });
})();
