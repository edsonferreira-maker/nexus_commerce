"""Extract editable product drafts from public product metadata or supplied HTML."""

from __future__ import annotations

import hashlib
import ipaddress
import math
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
    from universal_import import validate_url
    validate_url(value)
    return 'generic', host


class SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        if market_for_url(newurl)[0] == 'mercadolivre':
            raise ImportErrorMessage('Mercado Livre exige a API autorizada.')
        return super().redirect_request(request, fp, code, msg, headers, newurl)


def fetch_html(url: str, config=None) -> str:
    from universal_import import safe_fetch
    return safe_fetch(url, config or {})


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
    raise ImportErrorMessage('Captura automatizada por navegador desativada. Use API ou HTML autorizado.')


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
    if not math.isfinite(amount) or amount < 0:
        return ''
    prefix = {'BRL': 'R$', 'USD': 'US$', 'EUR': '€'}.get(currency.upper(), currency.upper() + ' ')
    return prefix + ' ' + f'{amount:,.2f}'.replace(',', '_').replace('.', ',').replace('_', '.')


def blank_product(url: str, notice: str = '') -> dict:
    _, market = market_for_url(url)
    canonical = urllib.parse.urlsplit(url)._replace(fragment='').geturl()
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
        'rating': '',
        'reviewCount': 0,
        'stock': -1,
        'brand': '',
        'installmentText': '',
        'shippingText': '',
        'variations': [],
        'redirect': True,
        'importNotice': notice,
    }


def mercado_livre_link_fallback(url: str) -> dict:
    """Build a useful draft from a Mercado Livre permalink when its anti-bot page wins.

    Mercado Livre keeps the human-readable product title in all supported permalink
    formats. This is deliberately a last resort: it never invents a price, photo, or
    stock value, but it lets the editor open instead of turning a temporary crawler
    block into a dead end.
    """
    market, _ = market_for_url(url)
    if market != 'mercadolivre':
        raise ImportErrorMessage('O fallback pelo link só é compatível com o Mercado Livre.')
    parsed = urllib.parse.urlsplit(url.strip())
    segments = [urllib.parse.unquote(value) for value in parsed.path.split('/') if value]
    slug = ''
    for index, segment in enumerate(segments):
        if re.fullmatch(r'MLBU[-_]?\d{7,15}', segment, re.IGNORECASE):
            if index:
                previous = index - 2 if segments[index - 1].lower() == 'up' and index >= 2 else index - 1
                slug = segments[previous]
            break
        item = re.match(r'MLB[-_]?\d{7,15}-(.+?)(?:-_JM)?$', segment, re.IGNORECASE)
        if item:
            slug = item.group(1)
            break
        if re.fullmatch(r'MLB[-_]?\d{7,15}', segment, re.IGNORECASE) and index:
            slug = segments[index - 1]
            break
    title = ' '.join(part for part in re.split(r'[-_]+', slug) if part).strip()
    if not title:
        raise ImportErrorMessage('Não encontrei o título do produto no link informado.')
    title = title[:1].upper() + title[1:]
    title = re.sub(r'\biphone\b', 'iPhone', title, flags=re.IGNORECASE)
    title = re.sub(r'\bmagsafe\b', 'MagSafe', title, flags=re.IGNORECASE)
    draft = blank_product(url)
    draft.update({
        'name': title[:200],
        'description': (
            f'{title}. Confira preço, fotos e detalhes no anúncio original do Mercado Livre.'
        )[:10000],
        'importNotice': (
            'O Mercado Livre bloqueou a leitura automática de preço e fotos. '
            'O título foi recuperado do próprio link; complete os campos destacados antes de publicar.'
        ),
    })
    return draft


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
    price = price_text(offers.get('price') if offers.get('price') is not None else meta.get('product:price:amount') or meta.get('price'), currency)
    aggregate = product.get('aggregateRating') or {}
    if not isinstance(aggregate, dict):
        aggregate = {}
    rating = aggregate.get('ratingValue')
    try:
        rating_text = f'{float(rating):.1f}'.replace('.', ',') if rating is not None and math.isfinite(float(rating)) and 0 < float(rating) <= 5 else ''
    except (ValueError, TypeError):
        rating_text = ''
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
            'author': text_value(review.get('author')) or 'Autor não informado',
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
        'currency': currency or None,
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
