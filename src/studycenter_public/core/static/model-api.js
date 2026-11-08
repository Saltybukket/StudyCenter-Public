const provider = document.querySelector('[data-model-provider]');
if (provider) {
  const updateProviderPanels = () => {
    document.querySelectorAll('[data-provider-panel]').forEach(panel => {
      panel.hidden = panel.dataset.providerPanel !== provider.value;
    });
  };
  provider.addEventListener('change', updateProviderPanels);
  updateProviderPanels();
}

document.querySelector('[data-api-test]')?.addEventListener('submit', async event => {
  event.preventDefault();
  const form = event.currentTarget, button = form.querySelector('button');
  const result = form.querySelector('[data-api-test-result]');
  button.disabled = true; result.textContent = 'Verbindung wird geprüft …';
  try {
    const data = await api(form.action, {method: 'POST', body: new FormData(form)});
    result.textContent = data.message;
  } catch (error) { result.textContent = error.message || 'Verbindung unterbrochen. Bitte erneut prüfen.'; }
  finally { button.disabled = false; }
});
