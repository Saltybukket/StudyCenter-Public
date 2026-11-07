(() => {
  document.addEventListener('invalid',event=>{
    let parent=event.target.parentElement;
    while(parent){if(parent.tagName==='DETAILS')parent.open=true;parent=parent.parentElement;}
  },true);
  document.querySelectorAll('[data-settings-advanced]').forEach(details=>{details.classList.add('disclosure-ready');details.open=false;});
  const presets = [
    ['1440, 120', '1 Tag und 2 Stunden vorher'],
    ['1440', '1 Tag vorher'],
    ['120', '2 Stunden vorher'],
    ['60, 30', '1 Stunde und 30 Minuten vorher'],
    ['60', '1 Stunde vorher'],
    ['30', '30 Minuten vorher'],
    ['10, 0', '10 Minuten vorher und zum Beginn'],
    ['0', 'Zum Beginn']
  ];
  const canonical = value => value.split(',').map(part => part.trim()).join(',');
  document.querySelectorAll('[data-reminder-preset]').forEach(group => {
    const input = group.querySelector('input');
    const select = group.querySelector('select');
    const choice = group.querySelector('.reminder-choice');
    const custom = group.querySelector('.reminder-custom');
    if (!input || !select || !choice || !custom) return;
    presets.forEach(([value, label]) => select.add(new Option(label, value)));
    select.add(new Option('Eigene Zeiten', 'custom'));
    function reflectInput() {
      const match = presets.find(([value]) => canonical(value) === canonical(input.value));
      select.value = match ? match[0] : 'custom';
    }
    select.addEventListener('change', () => {
      if (select.value === 'custom') {
        custom.open = true;
        input.focus();
      } else {
        input.value = select.value;
        input.dispatchEvent(new Event('input', {bubbles: true}));
        custom.open = false;
      }
    });
    input.addEventListener('input', reflectInput);
    reflectInput();
    custom.open = select.value === 'custom';
    choice.hidden = false;
  });
})();
