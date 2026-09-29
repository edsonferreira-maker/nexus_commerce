"""Extract editable product drafts from public product metadata or supplied HTML."""

from __future__ import annotations

import hashlib
import ipaddress
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser


MARKETS = {
    'mercadolivre': ('Mercado Livre', ('mercadolivre.com.br', 'mercadolibre.com.br')),
    'shopee': ('Shopee', ('shopee.com.br', 'shopee.com')),
    'temu': ('Temu', ('temu.com', 'temu.com.br')),
    'aliexpress': ('AliExpress', ('aliexpress.com', 'aliexpress.us')),
    'amazon': ('Amazon', ('amazon.com.br', 'amazon.com')),
}
MAX_HTML = 8 * 1024 * 1024


class ImportErrorMessage(Exception):
    pass


def market_for_url(value: str) -> tuple[str, str]:
    parsed = urllib.parse.urlsplit(value.strip())
    host = (parsed.hostname or '').lower()
    if parsed.scheme != 'https' or parsed.port not in (None, 443) or parsed.username or parsed.password:
        raise ImportErrorMessage('Use um link HTTPS de produto de uma loja compatível.')
    for key, (label, domains) in MARKETS.items():
        if any(host == domain or host.endswith('.' + domain) for domain in domains):
            return key, label
    raise ImportErrorMessage('Link compatível: Mercado Livre, Shopee, Temu, AliExpress ou Amazon.')


class SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        market_for_url(newurl)
        return super().redirect_request(request, fp, code, msg, headers, newurl)


def fetch_html(url: str) -> str:
    market, _ = market_for_url(url)
    opener = urllib.request.build_opener(SafeRedirect)
    def read(user_agent: str) -> str:
        request = urllib.request.Request(url, headers={
            'User-Agent': user_agent,
            'Accept': 'text/html,application/xhtml+xml',
        })
        with opener.open(request, timeout=12) as response:
            final_path = urllib.parse.urlsplit(response.geturl()).path.lower()
            if any(part in final_path for part in (
                '/signin', '/login', '/captcha', '/challenge', '/verify',
                '/account-verification', '/suspicious-traffic',
            )):
                raise ImportErrorMessage('A loja exigiu login ou verificação para mostrar o produto.')
            if 'html' not in response.headers.get_content_type():
                raise ImportErrorMessage('A URL não retornou uma página HTML.')
            raw = response.read(MAX_HTML + 1)
            if len(raw) > MAX_HTML:
                raise ImportErrorMessage('A página é grande demais para importação automática.')
            return raw.decode(response.headers.get_content_charset() or 'utf-8', errors='replace')
    try:
        return read('Mozilla/5.0 (compatible; NexoCommerce/1.0)')
    except (urllib.error.HTTPError, OSError, UnicodeError, ImportErrorMessage) as error:
        if market == 'mercadolivre':
            try:
                # O Mercado Livre publica metadados completos para agentes de
                # pré-visualização de links mesmo quando bloqueia IPs de nuvem.
                return read(
                    'facebookexternalhit/1.1 '
                    '(+https://nexus-commerce-1n5m.onrender.com)'
                )
            except (urllib.error.HTTPError, OSError, UnicodeError, ImportErrorMessage):
                pass
        if isinstance(error, ImportErrorMessage):
            raise error
        if not isinstance(error, urllib.error.HTTPError):
            raise ImportErrorMessage(
                'Não foi possível ler a página diretamente. A inspeção pelo navegador será tentada automaticamente.'
            ) from error
        raise ImportErrorMessage(
            f'A loja bloqueou a leitura direta (HTTP {error.code}). A inspeção pelo navegador será tentada automaticamente.'
        ) from error


def augment_with_dom_snapshot(html: str, snapshot: dict, url: str) -> str:
    """Prepend structured product data collected from visible DOM elements."""
    if not isinstance(snapshot, dict):
        return html
    title = snapshot.get('title')
    description = snapshot.get('description')
    images = snapshot.get('images')
    if not isinstance(title, str):
        title = ''
    if not isinstance(description, str):
        description = ''
    if not isinstance(images, list):
        images = []
    images = [
        image for image in (safe_image(str(value), url) for value in images[:30])
        if image
    ]
    price = snapshot.get('priceAmount')
    currency = snapshot.get('priceCurrency')
    if not isinstance(price, str):
        price = ''
    if not isinstance(currency, str):
        currency = ''
    if not price:
        visible_price = snapshot.get('priceText')
        if isinstance(visible_price, str):
            match = re.search(r'(R\$|US\$|€)\s*([0-9.]+(?:,[0-9]{1,2})?)', visible_price)
            if match:
                currency = {'R$': 'BRL', 'US$': 'USD', '€': 'EUR'}[match.group(1)]
                price = match.group(2).replace('.', '').replace(',', '.')
    if not title and not images:
        return html
    product = {
        '@context': 'https://schema.org',
        '@type': 'Product',
        'name': title[:200],
        'description': description[:10000],
        'image': images[:10],
    }
    if price and currency:
        product['offers'] = {
            '@type': 'Offer',
            'price': price,
            'priceCurrency': currency,
        }
    encoded = json.dumps(product, ensure_ascii=False).replace('</', '<\\/')
    return f'<script type="application/ld+json">{encoded}</script>{html}'


def fetch_rendered_html(url: str) -> str:
    """Read the DOM after JavaScript executes, as shown in browser devtools."""
    market_for_url(url)
    try:
        from playwright.sync_api import Error as BrowserError
        from playwright.sync_api import TimeoutError as BrowserTimeout
        from playwright.sync_api import sync_playwright
    except ImportError as error:
        raise ImportErrorMessage(
            'O navegador de inspeção automática não está instalado no servidor.'
        ) from error
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            try:
                page = browser.new_page(
                    locale='pt-BR',
                    timezone_id='America/Sao_Paulo',
                    viewport={'width': 1440, 'height': 1000},
                    color_scheme='light',
                )
                def allow_request(route):
                    request_url = urllib.parse.urlsplit(route.request.url)
                    host = (request_url.hostname or '').lower()
                    try:
                        address = ipaddress.ip_address(host)
                    except ValueError:
                        private_host = host in ('localhost',) or host.endswith(('.local', '.internal'))
                    else:
                        private_host = not address.is_global
                    if request_url.scheme not in ('http', 'https') or private_host:
                        return route.abort()
                    if route.request.is_navigation_request():
                        try:
                            market_for_url(route.request.url)
                        except ImportErrorMessage:
                            return route.abort()
                    return route.continue_()

                page.route('**/*', allow_request)
                page.goto(url, wait_until='domcontentloaded', timeout=20000)
                try:
                    page.wait_for_load_state('networkidle', timeout=5000)
                except BrowserTimeout:
                    pass
                try:
                    page.locator('h1, [itemprop="name"], meta[property="og:title"]').first.wait_for(
                        state='attached', timeout=5000,
                    )
                except BrowserTimeout:
                    pass
                page.evaluate('window.scrollTo(0, Math.min(document.body.scrollHeight, 1600))')
                page.wait_for_timeout(750)
                final_url = page.url
                market_for_url(final_url)
                path = urllib.parse.urlsplit(final_url).path.lower()
                if any(part in path for part in (
                    '/signin', '/login', '/captcha', '/challenge', '/verify',
                    '/account-verification', '/suspicious-traffic',
                )):
                    raise ImportErrorMessage(
                        'A loja exigiu login ou verificação e bloqueou a inspeção automática.'
                    )
                html = page.content()
                snapshot = page.evaluate("""
                    () => {
                      const content = (selector) =>
                        document.querySelector(selector)?.getAttribute('content')?.trim() || '';
                      const text = (selectors) => {
                        for (const selector of selectors) {
                          const value = document.querySelector(selector)?.textContent?.trim();
                          if (value) return value.replace(/\\s+/g, ' ');
                        }
                        return '';
                      };
                      const title = content('meta[property="og:title"]') || text([
                        'h1[itemprop="name"]', '[data-testid*="title"]', 'main h1', 'h1'
                      ]);
                      const description = content('meta[property="og:description"]') ||
                        content('meta[name="description"]') || text([
                          '[itemprop="description"]', '[data-testid*="description"]',
                          '[class*="description"]'
                        ]);
                      const priceAmount = content('meta[itemprop="price"]') ||
                        content('meta[property="product:price:amount"]');
                      const priceCurrency = content('meta[itemprop="priceCurrency"]') ||
                        content('meta[property="product:price:currency"]');
                      const priceText = text([
                        '[itemprop="price"]', '[data-testid*="price"]',
                        '[class*="price-tag"]', '[class*="price"]'
                      ]);
                      const images = [];
                      const add = (value) => {
                        if (value && !images.includes(value)) images.push(value);
                      };
                      add(content('meta[property="og:image"]'));
                      add(content('meta[name="twitter:image"]'));
                      for (const image of document.querySelectorAll('main img, [role="main"] img, img[itemprop="image"]')) {
                        if (image.naturalWidth >= 160 || image.naturalHeight >= 160 || image.alt?.includes(title)) {
                          add(image.currentSrc || image.dataset.zoomImage || image.dataset.src || image.src);
                        }
                        if (images.length >= 20) break;
                      }
                      return {title, description, priceAmount, priceCurrency, priceText, images};
                    }
                """)
                html = augment_with_dom_snapshot(html, snapshot, final_url)
                if len(html.encode('utf-8')) > MAX_HTML:
                    raise ImportErrorMessage('A página renderizada é grande demais para importar.')
                return html
            finally:
                browser.close()
    except BrowserError as error:
        raise ImportErrorMessage(
            'A inspeção automática pelo navegador não conseguiu abrir a página.'
        ) from error


class MetadataParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.meta: dict[str, str] = {}
        self.scripts: list[str] = []
        self.headings: list[str] = []
        self.images: list[tuple[str, str, str]] = []
        self._jsonld = False
        self._buffer: list[str] = []
        self._heading = False
        self._heading_buffer: list[str] = []

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag == 'meta':
            key = (values.get('property') or values.get('name') or values.get('itemprop') or '').lower()
            content = values.get('content') or ''
            if key and content:
                self.meta.setdefault(key, content.strip())
        elif values.get('itemprop') and values.get('content'):
            self.meta.setdefault(values['itemprop'].lower(), values['content'].strip())
        if tag == 'img':
            try:
                width = int(values.get('width') or 0)
                height = int(values.get('height') or 0)
            except ValueError:
                width = height = 0
            if not (0 < width < 100 or 0 < height < 100):
                source = values.get('data-zoom-image') or values.get('data-src') or values.get('src') or ''
                self.images.append((source, values.get('alt') or '', values.get('itemprop') or ''))
        elif tag == 'h1':
            self._heading = True
            self._heading_buffer = []
        elif tag == 'script' and values.get('type', '').lower() == 'application/ld+json':
            self._jsonld = True
            self._buffer = []

    def handle_data(self, data):
        if self._jsonld:
            self._buffer.append(data)
        if self._heading:
            self._heading_buffer.append(data)

    def handle_endtag(self, tag):
        if tag == 'script' and self._jsonld:
            self.scripts.append(''.join(self._buffer))
            self._jsonld = False
            self._buffer = []
        elif tag == 'h1' and self._heading:
            heading = ' '.join(''.join(self._heading_buffer).split())
            if heading:
                self.headings.append(heading)
            self._heading = False
            self._heading_buffer = []


def product_nodes(value):
    if isinstance(value, list):
        for child in value:
            yield from product_nodes(child)
    elif isinstance(value, dict):
        kinds = value.get('@type') or []
        if isinstance(kinds, str):
            kinds = [kinds]
        if any(kind == 'Product' or kind.endswith('/Product') for kind in kinds if isinstance(kind, str)):
            yield value
        for key in ('@graph', 'mainEntity', 'itemListElement'):
            if key in value:
                yield from product_nodes(value[key])


def text_value(value) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        return text_value(value.get('name') or value.get('text') or value.get('@value'))
    return ''


def image_values(value):
    if isinstance(value, list):
        for entry in value:
            yield from image_values(entry)
    elif isinstance(value, dict):
        yield from image_values(value.get('url') or value.get('contentUrl'))
    elif isinstance(value, str):
        yield value


def safe_image(value: str, base_url: str) -> str | None:
    if not value.strip():
        return None
    url = urllib.parse.urljoin(base_url, value.strip())
    try:
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme == 'https' and parsed.hostname and parsed.port in (None, 443) and not parsed.username:
            return url
    except ValueError:
        pass
    return None


def price_text(value, currency: str) -> str:
    if value in (None, '') or not currency:
        return ''
    try:
        amount = float(str(value).replace(',', '.'))
    except ValueError:
        return ''
    if amount < 0:
        return ''
    prefix = {'BRL': 'R$', 'USD': 'US$', 'EUR': '€'}.get(currency.upper(), currency.upper() + ' ')
    return prefix + ' ' + f'{amount:,.2f}'.replace(',', '_').replace('.', ',').replace('_', '.')


def blank_product(url: str, notice: str = '') -> dict:
    _, market = market_for_url(url)
    canonical = urllib.parse.urlsplit(url)._replace(query='', fragment='').geturl()
    digest = hashlib.sha256(canonical.encode()).hexdigest()[:16]
    return {
        'id': 'SRC' + digest.upper(),
        'slug': 'p-' + digest,
        'name': '',
        'storeName': 'Minha loja',
        'price': '',
        'market': market,
        'sourceUrl': url,
        'images': [],
        'description': '',
        'reviews': [],
        'rating': '0',
        'reviewCount': 0,
        'stock': -1,
        'brand': '',
        'installmentText': '',
        'shippingText': '',
        'variations': [],
        'redirect': True,
        'importNotice': notice,
    }


def extract_product(url: str, html: str) -> dict:
    draft = blank_product(url)
    market = draft['market']
    if len(html.encode('utf-8')) > MAX_HTML:
        raise ImportErrorMessage('O HTML informado é grande demais.')
    parser = MetadataParser()
    parser.feed(html)
    nodes = []
    for source in parser.scripts:
        try:
            nodes.extend(product_nodes(json.loads(source)))
        except (json.JSONDecodeError, ValueError):
            continue
    product = next((node for node in nodes if node.get('name') or node.get('image')), {})
    meta = parser.meta
    title = text_value(product.get('name')) or meta.get('og:title') or meta.get('twitter:title') or next(iter(parser.headings), '')
    description = text_value(product.get('description')) or meta.get('og:description') or meta.get('description') or ''
    images = []
    for candidate in [*image_values(product.get('image')), meta.get('og:image', ''), meta.get('twitter:image', '')]:
        image = safe_image(candidate, url)
        if image and image not in images:
            images.append(image)
    title_folded = title.casefold()
    for candidate, alt, itemprop in parser.images:
        alt_folded = alt.strip().casefold()
        if itemprop.lower() != 'image' and not (
            len(alt_folded) >= 8 and title_folded
            and (alt_folded in title_folded or title_folded in alt_folded)
        ):
            continue
        image = safe_image(candidate, url)
        if image and image not in images:
            images.append(image)
    if not title and not images:
        raise ImportErrorMessage(
            'A página não trouxe dados do produto. A loja pode ter exigido login ou bloqueado a leitura. '
            'A inspeção automática do conteúdo também não encontrou os dados.'
        )
    offers = product.get('offers') or {}
    if isinstance(offers, list):
        offers = next((offer for offer in offers if isinstance(offer, dict)), {})
    if not isinstance(offers, dict):
        offers = {}
    currency = text_value(offers.get('priceCurrency')) or meta.get('product:price:currency') or meta.get('pricecurrency') or ''
    price = price_text(offers.get('price') or meta.get('product:price:amount') or meta.get('price'), currency)
    aggregate = product.get('aggregateRating') or {}
    if not isinstance(aggregate, dict):
        aggregate = {}
    rating = aggregate.get('ratingValue')
    try:
        rating_text = f'{float(rating):.1f}'.replace('.', ',') if rating is not None else '0'
    except (ValueError, TypeError):
        rating_text = '0'
    reviews = []
    raw_reviews = product.get('review') or []
    if isinstance(raw_reviews, dict):
        raw_reviews = [raw_reviews]
    for review in raw_reviews[:10] if isinstance(raw_reviews, list) else []:
        if not isinstance(review, dict):
            continue
        comment = text_value(review.get('reviewBody') or review.get('description'))
        if not comment:
            continue
        score = review.get('reviewRating') or {}
        try:
            stars = int(float(score.get('ratingValue') or 0)) if isinstance(score, dict) else 0
        except (ValueError, TypeError):
            stars = 0
        reviews.append({
            'author': text_value(review.get('author')) or f'Comprador da {market}',
            'date': text_value(review.get('datePublished')),
            'comment': comment,
            'stars': stars if 1 <= stars <= 5 else 0,
        })
    count = aggregate.get('reviewCount') or aggregate.get('ratingCount') or len(reviews)
    try:
        count = max(int(count), len(reviews))
    except (ValueError, TypeError):
        count = len(reviews)
    draft.update({
        'name': title[:200],
        'price': price,
        'images': images[:10],
        'description': description[:10000],
        'reviews': reviews,
        'rating': rating_text,
        'reviewCount': count,
        'stock': -1,
        'brand': text_value(product.get('brand'))[:100],
        'installmentText': '',
        'shippingText': '',
        'variations': [],
        'importNotice': 'Alguns dados não foram encontrados. Complete os campos antes de publicar.' if not title or not images or not price else '',
    })
    return draft
