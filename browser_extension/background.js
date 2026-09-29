const NEXO_ORIGIN = 'https://nexus-commerce-1n5m.onrender.com';
const ALLOWED_HOSTS = ['mercadolivre.com.br', 'mercadolibre.com.br'];

function validProductUrl(value) {
  try {
    const url = new URL(value);
    return url.protocol === 'https:' && ALLOWED_HOSTS.some(
      (host) => url.hostname === host || url.hostname.endsWith(`.${host}`),
    );
  } catch (_) {
    return false;
  }
}

function hasProduct(html) {
  if (typeof html !== 'string' || html.length < 5000) return false;
  if (/\/gz\/account-verification|Olá! Para continuar, acesse sua conta/i.test(html)) return false;
  return /application\/ld\+json|ui-pdp-title|itemprop=["']price["']/i.test(html);
}

async function fetchWithBrowserSession(url) {
  const response = await fetch(url, {
    credentials: 'include',
    cache: 'no-store',
    headers: {
      Accept: 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
      'Accept-Language': 'pt-BR,pt;q=0.9',
    },
  });
  const finalUrl = new URL(response.url);
  if (!validProductUrl(finalUrl.href)) throw new Error('Redirecionamento não permitido.');
  const html = await response.text();
  if (!hasProduct(html)) throw new Error('A resposta não contém o produto.');
  return html;
}

function waitUntilLoaded(tabId) {
  return new Promise((resolve, reject) => {
    const timeout = setTimeout(() => {
      chrome.tabs.onUpdated.removeListener(listener);
      reject(new Error('Tempo esgotado ao abrir o produto.'));
    }, 20000);
    const listener = (updatedId, info) => {
      if (updatedId !== tabId || info.status !== 'complete') return;
      clearTimeout(timeout);
      chrome.tabs.onUpdated.removeListener(listener);
      resolve();
    };
    chrome.tabs.onUpdated.addListener(listener);
  });
}

async function captureRenderedTab(url) {
  const tab = await chrome.tabs.create({url, active: false});
  try {
    await waitUntilLoaded(tab.id);
    const [{result}] = await chrome.scripting.executeScript({
      target: {tabId: tab.id},
      func: async () => {
        const delay = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds));
        await delay(1200);
        window.scrollTo(0, Math.min(document.body.scrollHeight, 1800));
        await delay(900);
        return {
          url: location.href,
          html: document.documentElement.outerHTML,
        };
      },
    });
    if (!result || !validProductUrl(result.url) || !hasProduct(result.html)) {
      throw new Error('O Mercado Livre não mostrou os dados do produto nesta sessão.');
    }
    return result.html;
  } finally {
    if (tab.id) await chrome.tabs.remove(tab.id).catch(() => {});
  }
}

async function captureProduct(url) {
  try {
    return await fetchWithBrowserSession(url);
  } catch (_) {
    return captureRenderedTab(url);
  }
}

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message?.type !== 'capture-product') return false;
  if (!sender.tab?.url?.startsWith(`${NEXO_ORIGIN}/`) || !validProductUrl(message.url)) {
    sendResponse({error: 'Solicitação de captura inválida.'});
    return false;
  }
  captureProduct(message.url)
    .then((html) => sendResponse({html}))
    .catch((error) => sendResponse({error: String(error?.message || error)}));
  return true;
});
