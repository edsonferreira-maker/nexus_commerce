"""Authorized source adapters and DNS-pinned HTTPS; standard-library HTML parsing."""
import hashlib
import http.client
import ipaddress
import json
import math
import re
import socket
import ssl
import time
import urllib.parse
from datetime import datetime, timezone
from html.parser import HTMLParser
from typing import Protocol

from page_import import ImportErrorMessage, MAX_HTML, extract_product, market_for_url, product_nodes
from mercadolivre import fetch_product, item_id_from_url, is_catalog_product_url, selected_item_from_url, approved_media


class SourceError(ImportErrorMessage, ValueError):
    pass


def validate_host(host):
    if not isinstance(host, str) or len(host) > 253 or host != host.strip():
        raise SourceError('Domínio inválido.')
    try:
        host = host.encode('idna').decode().lower()
    except UnicodeError:
        raise SourceError('Domínio inválido.')
    if not re.fullmatch(r'(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z][a-z0-9-]{1,62}', host) or host.endswith(('.local', '.localhost', '.internal', '.test', '.invalid')):
        raise SourceError('Use um domínio público exato, sem URL, IP ou curinga.')
    return host


def validate_url(url, allowed_hosts=None):
    if not isinstance(url, str) or len(url) > 2048 or any(ord(c) < 33 or ord(c) == 127 for c in url) or '\\' in url:
        raise SourceError('URL HTTPS inválida.')
    try:
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme != 'https' or not parsed.hostname or parsed.username is not None or parsed.password is not None or parsed.port not in (None, 443):
            raise ValueError
        host = validate_host(parsed.hostname)
    except ValueError as error:
        raise SourceError('Use uma URL HTTPS pública, sem credenciais ou porta especial.') from error
    if allowed_hosts is not None and host not in allowed_hosts:
        raise SourceError('Domínio não autorizado na Central de Integrações.')
    return parsed, host


def public_addresses(host):
    try:
        addresses = list(dict.fromkeys(info[4][0] for info in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)))
    except OSError as error:
        raise SourceError('Não foi possível resolver o domínio autorizado.') from error
    def safe_address(address):
        ip = ipaddress.ip_address(address)
        if not ip.is_global:
            return False
        if isinstance(ip, ipaddress.IPv6Address):
            if ip.ipv4_mapped or ip.sixtofour or ip.teredo:
                return False
            if ip in ipaddress.ip_network('64:ff9b::/96') or ip in ipaddress.ip_network('64:ff9b:1::/48'):
                return False
        return True
    if not addresses or not all(safe_address(address) for address in addresses):
        raise SourceError('Destino privado, reservado ou ambíguo bloqueado (SSRF).')
    return addresses


class PinnedHTTPS(http.client.HTTPSConnection):
    def __init__(self, host, address):
        super().__init__(host, timeout=12, context=ssl.create_default_context())
        self.address = address

    def connect(self):
        # Hostname remains the original for Host/SNI/certificate verification. DNS is not repeated.
        sock = socket.create_connection((self.address, 443), timeout=self.timeout)
        try:
            self.sock = self._context.wrap_socket(sock, server_hostname=self.host)
        except BaseException:
            sock.close()
            raise


def safe_fetch(url, config):
    hosts = config.get('allowedHosts', [])
    if market_for_url(url)[0] == 'mercadolivre':
        raise SourceError('Mercado Livre exige a API autorizada.')
    initial_host = validate_url(url, hosts)[1]
    if not config.get('authorizationConfirmed'):
        raise SourceError('Confirme autorização de leitura na Central de Integrações.')
    for _ in range(4):
        parsed, host = validate_url(url, hosts)
        if market_for_url(url)[0] == 'mercadolivre':
            raise SourceError('Mercado Livre exige a API oficial; leitura de HTML não permitida.')
        connection = PinnedHTTPS(host, public_addresses(host)[0])
        headers = {'Accept': 'text/html,application/xhtml+xml', 'User-Agent': 'NexoCommerce/1.0', **config.get('headers', {})}
        # Do not forward credentials across redirects, even to another authorized host.
        if config.get('apiKey') and host == initial_host:
            headers['Authorization'] = 'Bearer ' + config['apiKey']
        try:
            connection.request('GET', urllib.parse.urlunsplit(('', '', parsed.path or '/', parsed.query, '')), headers=headers)
            response = connection.getresponse()
            if response.status in (301, 302, 303, 307, 308):
                url = urllib.parse.urljoin(url, response.getheader('Location', ''))
                validate_url(url, hosts)
                continue
            if response.status != 200:
                messages = {401: 'Autorização ausente ou expirada.', 403: 'A fonte recusou a leitura. Verifique a permissão.', 404: 'Produto não encontrado.', 429: 'Limite da fonte atingido. Respeite Retry-After e tente mais tarde.'}
                raise SourceError(messages.get(response.status, 'Falha na fonte.') + f' HTTP {response.status}.')
            if any(part in parsed.path.lower() for part in ('/login', '/signin', '/captcha', '/challenge', '/verify')):
                raise SourceError('A fonte exige login ou verificação. Use uma integração autorizada.')
            if 'html' not in response.getheader('Content-Type', '').lower():
                raise SourceError('A fonte não retornou HTML de produto.')
            if response.getheader('Content-Encoding', 'identity') not in ('', 'identity'):
                raise SourceError('Codificação da fonte não suportada.')
            body = response.read(MAX_HTML + 1)
            if len(body) > MAX_HTML:
                raise SourceError('HTML excede o limite de 8 MiB.')
            return body.decode('utf-8', 'replace')
        except (OSError, http.client.HTTPException) as error:
            raise SourceError('Falha de rede ou TLS ao consultar a fonte autorizada.') from error
        finally:
            connection.close()
    raise SourceError('Limite de redirecionamentos excedido.')


def valid_selector(value):
    return isinstance(value, str) and len(value) <= 120 and bool(re.fullmatch(r'(?:[a-zA-Z][a-zA-Z0-9-]*|[.#][\w-]+|\[itemprop=["\'][\w-]+["\']\])', value))


class DOM(HTMLParser):
    """Small inert DOM for exact selectors and microdata; no JS, browser or new dependency."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = {'tag': '', 'attrs': {}, 'text': [], 'children': [], 'content': []}
        self.stack = [self.root]
        self.nodes = []

    def handle_starttag(self, tag, attrs):
        node = {'tag': tag, 'attrs': dict(attrs), 'text': [], 'children': [], 'content': []}
        if len(self.stack) > 256 or len(self.nodes) > 100000:
            raise SourceError('HTML excessivamente complexo para análise segura.')
        self.stack[-1]['children'].append(node)
        self.stack[-1]['content'].append(node)
        self.nodes.append(node)
        if tag not in ('area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta', 'param', 'source', 'track', 'wbr'):
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i]['tag'] == tag:
                del self.stack[i:]
                break

    def handle_data(self, text):
        self.stack[-1]['text'].append(text)
        self.stack[-1]['content'].append(text)

    def select(self, selector, nodes=None):
        if not valid_selector(selector):
            return []
        def match(node):
            attrs = node['attrs']
            if selector.startswith('#'):
                return attrs.get('id') == selector[1:]
            if selector.startswith('.'):
                return selector[1:] in (attrs.get('class') or '').split()
            if selector.startswith('['):
                return selector[11:-2] in (attrs.get('itemprop') or '').split()
            return node['tag'] == selector.lower()
        return [node for node in (self.nodes if nodes is None else nodes) if match(node)]

    def text(self, node):
        if node['tag'] in ('script', 'style'):
            return ''
        return ' '.join(part if isinstance(part, str) else self.text(part) for part in node['content']).strip()

    def value(self, node):
        return node['attrs'].get('content') or self.text(node)

    def descendants(self, node):
        return [child for entry in node['children'] for child in [entry, *self.descendants(entry)]]


def decorate(product, source, paths=None, errors=None):
    stamp = datetime.now(timezone.utc).isoformat()
    paths, errors = paths or {}, errors or {}
    mapping = {'name': 'item.title', 'price': 'item.price', 'images': 'item.pictures[].secure_url', 'description': 'description.plain_text', 'rating': 'reviews.rating_average', 'reviews': 'reviews.reviews[]', 'variations': 'item.variations'}
    diagnostics = {}
    for field in ('name', 'price', 'images', 'description', 'rating', 'reviews', 'variations'):
        value = product.get(field)
        if field == 'rating' and value in ('0', 0):
            product['rating'] = value = ''
        existing = (product.get('sourceStatus') or {}).get('reviews' if field in ('rating', 'reviews') else field, {})
        status = existing.get('state') or ('available' if value not in (None, '', []) else 'absent')
        diagnostics[field] = {'value': value, 'source': source if value not in (None, '', []) else 'unavailable',
                              'selector_or_path': paths.get(field) or (mapping.get(field) if source == 'api' else None),
                              'retrieved_at': stamp, 'confidence': 0.0 if value in (None, '', []) else (1.0 if source == 'api' else (0.9 if paths.get(field, '').startswith('jsonld') else 0.7)),
                              'status': status, 'error': errors.get(field) or existing.get('reason') or None}
    product.update(fieldDiagnostics=diagnostics, fetchedAt=stamp, syncedAt=time.time(), source=product.get('source') or ('user_supplied' if source == 'user_supplied' else 'generic'),
                   missingFields=[k for k, v in diagnostics.items() if v['status'] != 'available'])
    product['sourceStatus'] = {**product.get('sourceStatus', {}), **{k: {'state': v['status'], 'reason': v['error']} for k, v in diagnostics.items()}}
    product.setdefault('imageDetails', [{'url': u, 'order': i, 'alt': product.get('name'), 'provenance': source} for i, u in enumerate(product.get('images', []))])
    product.setdefault('provenance', {}).update({k: v['source'] for k, v in diagnostics.items()})
    return product


class SourceAdapter(Protocol):
    capabilities: dict
    def canHandle(self, url): ...
    def resolve(self, url): ...
    def extract(self, url, **options): ...
    def diagnose(self, product): ...


class GenericAdapter:
    capabilities = {'html': True, 'jsonld': True, 'openGraph': True, 'microdata': True, 'selectors': True, 'javascript': False}

    def canHandle(self, url):
        return market_for_url(url)[0] != 'mercadolivre'

    def resolve(self, url):
        return validate_url(url)[0].geturl()

    def extract(self, url, config=None, html=None):
        config = config or {}
        manual_html = html is not None
        self.resolve(url)
        if not self.canHandle(url):
            raise SourceError('Mercado Livre exige API oficial.')
        if html is None:
            html = safe_fetch(url, config)
        elif not isinstance(html, str) or len(html.encode()) > MAX_HTML:
            raise SourceError('HTML autorizado inválido ou maior que 8 MiB.')
        dom = DOM()
        dom.feed(html)
        paths, fallback = {}, {}
        # Microdata only inside a Product scope. Do not confuse seller/recommendations with product.
        scope = next((n for n in dom.nodes if 'Product' in (n['attrs'].get('itemtype') or '').split('/')[-1]), None)
        nodes = dom.descendants(scope) if scope else []
        for field, prop in (('name', 'name'), ('description', 'description'), ('price', 'price'), ('currency', 'priceCurrency')):
            found = dom.select('[itemprop="' + prop + '"]', nodes)
            if found:
                fallback[field] = dom.value(found[0])
                paths[field] = '[itemprop="' + prop + '"]'
        for field, selector in config.get('selectors', {}).items():
            found = dom.select(selector)
            if found and field != 'images':
                fallback[field] = dom.value(found[0])
                paths[field] = selector
        # Existing parser handles JSON-LD and OG. Synthesize only missing structural fallbacks.
        synthetic = {'@type': 'Product', **{k: fallback[k] for k in ('name', 'description') if k in fallback},
                     'offers': {'price': fallback.get('price'), 'priceCurrency': fallback.get('currency')}}
        try:
            product = extract_product(url, html)
        except ImportErrorMessage:
            product = extract_product(url, html + '<script type="application/ld+json">' + json.dumps(synthetic) + '</script>')
        from page_import import MetadataParser, price_text, safe_image
        metadata = MetadataParser()
        metadata.feed(html)
        json_product = {}
        for script in metadata.scripts:
            try:
                json_product = next(iter(product_nodes(json.loads(script))), {})
                if json_product:
                    break
            except (ValueError, TypeError):
                pass
        for field, key in (('name', 'name'), ('description', 'description'), ('images', 'image'), ('reviews', 'review'), ('rating', 'aggregateRating'), ('price', 'offers')):
            if json_product.get(key):
                paths[field] = 'jsonld.Product.' + key
            elif product.get(field):
                paths.setdefault(field, 'meta.OpenGraph' if field != 'images' else 'img[itemprop="image"] or meta.og:image')
        for field in ('name', 'description'):
            if fallback.get(field) and (field in config.get('selectors', {}) or not product.get(field)):
                product[field] = fallback[field][:200 if field == 'name' else 10000]
                if field in config.get('selectors', {}):
                    paths[field] = config['selectors'][field]
        if fallback.get('price') and (config.get('selectors', {}).get('price') or not product.get('price')):
            product['price'] = price_text(fallback['price'], fallback.get('currency', ''))
            paths['price'] = config.get('selectors', {}).get('price') or '[itemprop="price"]'
        selector = config.get('selectors', {}).get('images') or '[itemprop="image"]'
        image_nodes = dom.select(selector, None if config.get('selectors', {}).get('images') else nodes)
        if not config.get('selectors', {}).get('images'):
            title = product.get('name', '').casefold()
            image_nodes += [n for n in dom.select('img') if n not in image_nodes and title and (n['attrs'].get('alt') or '').strip().casefold() == title]
        for node in image_nodes:
            attrs = node['attrs']
            candidates = [attrs.get('data-src') or attrs.get('src') or attrs.get('content') or attrs.get('href') or '']
            candidates += [part.strip().split()[0] for part in (attrs.get('data-srcset') or attrs.get('srcset') or '').split(',') if part.strip()]
            for candidate in candidates:
                image = safe_image(candidate, url)
                if image and image not in product['images']:
                    product['images'].append(image)
                    paths['images'] = selector + '/src|data-src|srcset'
        def prop_value(prop, scope_nodes):
            found = dom.select('[itemprop="' + prop + '"]', scope_nodes)
            return dom.value(found[0]) if found else ''
        # Microdata reviews remain scoped to the Product, independently of seller reputation.
        if not product.get('rating'):
            ratings = dom.select('[itemprop="aggregateRating"]', nodes)
            if ratings:
                rating_nodes = dom.descendants(ratings[0])
                try:
                    rating = float(prop_value('ratingValue', rating_nodes))
                    best = float(prop_value('bestRating', rating_nodes) or '5')
                    if math.isfinite(rating) and best == 5 and 0 < rating <= 5:
                        product['rating'] = f'{rating:.1f}'.replace('.', ',')
                        paths['rating'] = '[itemprop="aggregateRating"] [itemprop="ratingValue"]'
                    count = int(prop_value('reviewCount', rating_nodes) or prop_value('ratingCount', rating_nodes) or '0')
                    product['reviewCount'] = max(0, count)
                except (TypeError, ValueError, OverflowError):
                    pass
        if not product.get('reviews'):
            reviews = []
            for review_node in dom.select('[itemprop="review"]', nodes)[:10]:
                review_nodes = dom.descendants(review_node)
                comment = prop_value('reviewBody', review_nodes) or prop_value('description', review_nodes)
                if not comment:
                    continue
                try:
                    score = float(prop_value('ratingValue', review_nodes))
                    stars = int(score) if math.isfinite(score) and 1 <= score <= 5 else 0
                except (ValueError, TypeError):
                    stars = 0
                reviews.append({'comment': comment[:10000], 'author': prop_value('author', review_nodes) or 'Autor não informado', 'date': prop_value('datePublished', review_nodes), 'stars': stars})
            if reviews:
                product['reviews'] = reviews
                product['reviewCount'] = max(len(reviews), product.get('reviewCount', 0))
                paths['reviews'] = '[itemprop="review"] [itemprop="reviewBody"]'
        # Structural variants; never infer stock, ratings or reviews.
        variants = json_product.get('hasVariant') or []
        if isinstance(variants, dict):
            variants = [variants]
        product['variations'] = [str(v.get('name'))[:120] for v in variants if isinstance(v, dict) and isinstance(v.get('name'), str)][:12] if isinstance(variants, list) else []
        if product['variations']:
            paths['variations'] = 'jsonld.Product.hasVariant[].name'
        media_hosts = config.get('mediaHosts') or [validate_url(url)[1]]
        images = []
        for image in product['images'][:10]:
            try:
                validate_url(image, media_hosts)
                images.append(image)
            except SourceError:
                pass
        blocked = len(images) != len(product['images'][:10])
        generator = metadata.meta.get('generator', '').lower()
        platform = 'shopify' if 'shopify' in generator or 'cdn.shopify.com' in html else 'woocommerce' if 'woocommerce' in generator or 'woocommerce' in html.lower() else 'generic'
        product.update(platform=platform, images=images, allowedMediaHosts=media_hosts, inputUrl=url,
                       sourceUrl=url, authorization={'automatedRead': not manual_html, 'manualHtml': manual_html})
        return decorate(product, 'authorized_html', paths, {'images': 'Origem de mídia fora da allowlist.'} if blocked else {})

    def diagnose(self, product):
        return product['fieldDiagnostics']


class MercadoLivreAdapter:
    capabilities = {'api': True, 'html': False, 'oauth2': True}

    def canHandle(self, url):
        return market_for_url(url)[0] == 'mercadolivre'

    def resolve(self, url):
        return item_id_from_url(url)

    def extract(self, url, token, review_pages=1):
        selected = selected_item_from_url(url)
        item = self.resolve(url)
        if item.startswith('MLBU') and selected:
            item = selected
        product = fetch_product(item, token, catalog=is_catalog_product_url(url), selected_item=selected, review_pages=review_pages)
        product['inputUrl'] = url
        return decorate(product, 'api')

    def diagnose(self, product):
        return product['fieldDiagnostics']


ADAPTERS = [MercadoLivreAdapter(), GenericAdapter()]


def adapter_for(url):
    validate_url(url)
    return next(adapter for adapter in ADAPTERS if adapter.canHandle(url))
