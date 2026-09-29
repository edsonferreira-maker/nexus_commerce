const NEXO_ORIGIN = 'https://nexus-commerce-1n5m.onrender.com';

window.addEventListener('message', (event) => {
  let message;
  try {
    message = JSON.parse(event.data);
  } catch (_) {
    return;
  }
  if (event.source !== window || event.origin !== NEXO_ORIGIN) return;
  if (!message || message.source !== 'nexo-commerce' || message.type !== 'capture-product') return;

  window.postMessage(JSON.stringify({
    source: 'nexo-browser-extension',
    type: 'capture-ack',
    requestId: message.requestId,
  }), NEXO_ORIGIN);

  chrome.runtime.sendMessage({
    type: 'capture-product',
    requestId: message.requestId,
    url: message.url,
  }).then((response) => {
    window.postMessage(JSON.stringify({
      source: 'nexo-browser-extension',
      type: 'capture-result',
      requestId: message.requestId,
      html: response?.html || null,
      error: response?.error || null,
    }), NEXO_ORIGIN);
  }).catch(() => {
    window.postMessage(JSON.stringify({
      source: 'nexo-browser-extension',
      type: 'capture-result',
      requestId: message.requestId,
      html: null,
    }), NEXO_ORIGIN);
  });
});
