"""Mercado Livre OAuth and product reads through the official API."""

from __future__ import annotations

import json
import re
import random
import math
import time
from email.utils import parsedate_to_datetime
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime


API_ROOT = 'https://api.mercadolibre.com'
ITEM_ID = re.compile(r'MLB[-_]?([0-9]{7,15})(?![0-9A-Za-z])', re.IGNORECASE)
USER_PRODUCT_ID = re.compile(r'MLBU[-_]?([0-9]{7,15})(?![0-9A-Za-z])', re.IGNORECASE)


class MercadoLivreError(Exception):
    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


def item_id_from_url(value: str) -> str:
    """Only accept Mercado Livre Brazil URLs; never fetch an arbitrary user URL."""
    if ITEM_ID.fullmatch(value.strip()):
        return value.strip().upper().replace("-", "").replace("_", "")
    parsed = urllib.parse.urlsplit(value.strip())
    hostname = (parsed.hostname or '').lower()
    if parsed.scheme != 'https' or parsed.username or parsed.password or parsed.port not in (None, 443) or not (
        hostname == 'mercadolivre.com.br' or hostname.endswith('.mercadolivre.com.br')
        or hostname == 'mercadolibre.com.br' or hostname.endswith('.mercadolibre.com.br')
    ):
        raise MercadoLivreError('Informe o link de um produto do Mercado Livre Brasil.')
    match = USER_PRODUCT_ID.search(parsed.path.upper())
    if match:
        return 'MLBU' + match.group(1)
    match = ITEM_ID.search(parsed.path.upper())
    if not match:
        raise MercadoLivreError('Não encontrei o código MLB ou MLBU no link informado.')
    return 'MLB' + match.group(1)


def is_catalog_product_url(value: str) -> bool:
    path = urllib.parse.urlsplit(value.strip()).path
    return re.search(r'/p/MLB[-_]?\d{7,15}(?:/|$)', path, re.IGNORECASE) is not None


def selected_item_from_url(value: str) -> str | None:
    parsed = urllib.parse.urlsplit(value.strip())
    for section in (parsed.query, parsed.fragment):
        parameters = urllib.parse.parse_qs(section)
        for field in ('wid', 'item_id'):
            item = (parameters.get(field) or [''])[0]
            if ITEM_ID.fullmatch(item):
                return item.upper().replace('-', '').replace('_', '')
        for filters in parameters.get('pdp_filters') or []:
            match = re.search(r'(?:^|[|,])item_id:(MLB[-_]?\d{7,15})(?:$|[|,])', filters, re.IGNORECASE)
            if match:
                return match.group(1).upper().replace('-', '').replace('_', '')
    return None


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def request_json(url: str, token: str | None = None, form: dict | None = None):
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != 'https' or parsed.netloc != 'api.mercadolibre.com':
        raise MercadoLivreError('Destino de API inválido.')
    # Only idempotent GETs are retried; rotating OAuth grants must never be replayed.
    for attempt in range(3):
        try:
            return _request_json(url, token, form)
        except MercadoLivreError as error:
            if form is not None or error.status not in (429, 500, 502, 503, 504) or attempt == 2:
                raise
            delay = getattr(error, 'retry_after', None)
            if delay is not None and delay > 30:
                raise
            time.sleep(delay if delay is not None else 2 ** attempt + random.random())


def _request_json(url: str, token: str | None = None, form: dict | None = None):
    headers = {'Accept': 'application/json', 'User-Agent': 'NexoCommerce/1.0'}
    if token:
        headers['Authorization'] = f'Bearer {token}'
    body = None
    if form is not None:
        body = urllib.parse.urlencode(form).encode()
        headers['Content-Type'] = 'application/x-www-form-urlencoded'
    request = urllib.request.Request(url, data=body, headers=headers)
    try:
        with urllib.request.build_opener(NoRedirect()).open(request, timeout=15) as response:
            payload = json.loads(response.read(4 * 1024 * 1024).decode())
            if not isinstance(payload, dict):
                raise ValueError('Expected object')
            return payload
    except urllib.error.HTTPError as error:
        details = {}
        try:
            details = json.loads(error.read().decode('utf-8', 'replace'))
        except (ValueError, UnicodeError):
            pass
        if not isinstance(details, dict):
            details = {}
        messages = {
            400: 'Solicitação recusada. Verifique os dados ou reautorize a conta.',
            401: 'A autorização do Mercado Livre expirou. Reconecte a conta.',
            403: 'A conta conectada não tem permissão para ler este recurso.',
            404: 'Recurso não encontrado no Mercado Livre.',
            429: 'O Mercado Livre limitou as consultas. Tente novamente mais tarde.',
        }
        failure = MercadoLivreError(messages.get(error.code, f'Falha na API do Mercado Livre (HTTP {error.code}).'), error.code)
        retry = error.headers.get('Retry-After') if error.headers else None
        try:
            failure.retry_after = max(0, float(retry))
        except (TypeError, ValueError):
            try:
                failure.retry_after = max(0, parsedate_to_datetime(retry).timestamp() - time.time())
            except (TypeError, ValueError):
                failure.retry_after = None
        raise failure from error
    except (ValueError, UnicodeError) as error:
        raise MercadoLivreError('Formato inesperado na resposta do Mercado Livre.', 200) from error
    except OSError as error:
        raise MercadoLivreError('Não foi possível consultar o Mercado Livre agora.') from error


def exchange_code(client_id: str, client_secret: str, redirect_uri: str, code: str):
    return request_json(f'{API_ROOT}/oauth/token', form={
        'grant_type': 'authorization_code',
        'client_id': client_id,
        'client_secret': client_secret,
        'redirect_uri': redirect_uri,
        'code': code,
    })


def refresh_token(client_id: str, client_secret: str, token: str):
    payload = request_json(f'{API_ROOT}/oauth/token', form={
        'grant_type': 'refresh_token',
        'client_id': client_id,
        'client_secret': client_secret,
        'refresh_token': token,
    })
    if not isinstance(payload.get('refresh_token'), str) or not payload['refresh_token']:
        raise MercadoLivreError('Renovação incompleta. Reconecte a conta do Mercado Livre.')
    return payload


def money_brl(value) -> str:
    if value is None:
        return ''
    amount = number(value)
    if amount is None or amount < 0:
        return ''
    return 'R$ ' + f'{amount:,.2f}'.replace(',', '_').replace('.', ',').replace('_', '.')


def objects(value):
    return [entry for entry in value if isinstance(entry, dict)] if isinstance(value, list) else []


def number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    return value


def nonnegative_int(value, default=0):
    value = number(value)
    return int(value) if value is not None and value >= 0 else default


def product_from_api(item: dict, description: dict, opinions: dict) -> dict:
    if not all(isinstance(v, dict) for v in (item, description, opinions)):
        raise MercadoLivreError('Formato inesperado na resposta da API.')
    item = dict(item)
    for key in ('pictures', 'attributes', 'variations'):
        item[key] = objects(item.get(key))
    for variation in item['variations']:
        variation['attribute_combinations'] = objects(variation.get('attribute_combinations'))
        variation['picture_ids'] = variation.get('picture_ids') if isinstance(variation.get('picture_ids'), list) else []
    opinions = dict(opinions)
    opinions['reviews'] = objects(opinions.get('reviews'))
    opinions['paging'] = opinions.get('paging') if isinstance(opinions.get('paging'), dict) else {}
    plain_text = description.get('plain_text')
    description = {'plain_text': plain_text if isinstance(plain_text, str) else ''}
    images = [
        picture.get('secure_url') or picture.get('url')
        for picture in item.get('pictures') or []
    ]
    images = [
        url for url in images
        if isinstance(url, str)
        and approved_media(url)
    ][:10]
    if not images and approved_media(item.get('secure_thumbnail')):
        images = [item['secure_thumbnail']]
    reviews = []
    for opinion in (opinions.get('reviews') or [])[:100]:
        if opinion.get('status') not in (None, 'published'):
            continue
        comment = opinion.get('content') or opinion.get('title') or ''
        if not isinstance(comment, str) or not comment.strip():
            continue
        created = opinion.get('date_created') if isinstance(opinion.get('date_created'), str) else ''
        try:
            date = datetime.fromisoformat(created.replace('Z', '+00:00')).strftime('%d/%m/%Y')
        except ValueError:
            date = ''
        score = opinion.get('rate')
        reviews.append({
            'author': 'Comprador do Mercado Livre',
            'date': date,
            'comment': comment.strip(),
            'stars': score if isinstance(score, int) and not isinstance(score, bool) and 1 <= score <= 5 else None,
        })
    rating = opinions.get('rating_average')
    rating_text = f'{rating:.1f}'.replace('.', ',') if number(rating) is not None and 1 <= rating <= 5 else ''
    attributes = item.get('attributes') or []
    brand = next((attribute.get('value_name') for attribute in attributes if attribute.get('id') == 'BRAND'), '') or ''
    variants = []
    for variation in item.get('variations') or []:
        label = ' • '.join(
            f"{attribute.get('name')}: {attribute.get('value_name')}"
            for attribute in variation.get('attribute_combinations') or []
            if attribute.get('value_name')
        )
        if label and label not in variants:
            variants.append(label)
    item_id = str(item.get('id') or '')
    permalink = item.get('permalink') or ''
    link = urllib.parse.urlsplit(permalink) if isinstance(permalink, str) else None
    host = (link.hostname or '').lower() if link else ''
    if not ITEM_ID.fullmatch(item_id) or not link or link.username or link.password or link.port not in (None, 443) or link.scheme != 'https' or not (
        host == 'mercadolivre.com.br' or host.endswith('.mercadolivre.com.br')
        or host == 'mercadolibre.com.br' or host.endswith('.mercadolibre.com.br')
    ):
        raise MercadoLivreError('O Mercado Livre retornou dados incompletos para este anúncio.')
    return {
        'source': 'mercadolivre',
        'itemId': item_id,
        'catalogProductId': item.get('catalog_product_id'),
        'fetchedAt': datetime.now().astimezone().isoformat(),
        'attributes': attributes,
        'variationDetails': [{**v, 'images': [p.get('secure_url') for p in item.get('pictures') or [] if p.get('id') in (v.get('picture_ids') or []) and approved_media(p.get('secure_url'))]} for v in item.get('variations') or []],
        'provenance': {k: 'api' if value is not None and value != '' and value != [] and value != {} else 'unavailable' for k, value in {'name': item.get('title'), 'price': number(item.get('price')), 'images': images, 'description': description.get('plain_text'), 'reviews': opinions.get('reviews') or opinions.get('rating_average')}.items()},
        'imageDetails': [{'url': url, 'pictureId': next((p.get('id') for p in item['pictures'] if url in (p.get('secure_url'), p.get('url'))), None), 'order': i, 'alt': item.get('title'), 'provenance': 'api'} for i, url in enumerate(images)],
        'reviewDistribution': opinions.get('rating_levels'),
        'missingFields': [k for k, value in {'images': images, 'description': description.get('plain_text'), 'reviews': opinions, 'price': item.get('price')}.items() if value is None or value == [] or value == {} or value == ''],
        'id': item_id,
        'slug': item_id.lower(),
        'name': str(item.get('title') or ''),
        'storeName': 'Minha loja',
        'price': money_brl(item.get('price')) if item.get('currency_id') == 'BRL' else (f"{number(item.get('price'))} {item.get('currency_id') or '(moeda indisponível)'}" if number(item.get('price')) is not None else ''),
        'market': 'Mercado Livre',
        'sourceUrl': permalink,
        'images': images,
        'description': str(description.get('plain_text') or ''),
        'reviews': reviews,
        'rating': rating_text,
        'reviewCount': nonnegative_int(opinions['paging'].get('total')),
        'reviewScope': opinions.get('reviewable_object'),
        'reviewsPaging': opinions['paging'],
        'originalPrice': number(item.get('original_price')),
        'currency': item.get('currency_id'),
        'seller': {'id': item.get('seller_id')},
        'shipping': item.get('shipping') if isinstance(item.get('shipping'), dict) else {},
        'stock': nonnegative_int(item.get('available_quantity'), -1),
        'brand': brand,
        'installmentText': '',
        'shippingText': 'Frete grátis no Mercado Livre' if isinstance(item.get('shipping'), dict) and item['shipping'].get('free_shipping') else '',
        'variations': variants[:12],
        'redirect': True,
    }


def item_from_user_product(user_product_id: str, access_token: str) -> tuple[str, dict]:
    user_product = request_json(f'{API_ROOT}/user-products/{user_product_id}', access_token)
    seller_id = user_product.get('user_id')
    if not isinstance(seller_id, int) and not (isinstance(seller_id, str) and seller_id.isdigit()):
        raise MercadoLivreError('O Mercado Livre não informou o vendedor deste produto.')
    search = request_json(
        f'{API_ROOT}/users/{seller_id}/items/search?'
        + urllib.parse.urlencode({'user_product_id': user_product_id}),
        access_token,
    )
    candidates = [
        value for value in search.get('results') or []
        if isinstance(value, str) and ITEM_ID.fullmatch(value)
    ]
    if not candidates:
        raise MercadoLivreError('Este produto MLBU não possui um anúncio disponível para importar.')
    if len(candidates) != 1:
        raise MercadoLivreError('Selecione um anúncio MLB específico; este MLBU possui múltiplas ofertas.')
    return candidates[0], request_json(f'{API_ROOT}/items/{candidates[0]}', access_token)


def fetch_product(item_id: str, access_token: str, catalog: bool = False, selected_item: str | None = None, review_pages: int = 1) -> dict:
    if not (ITEM_ID.fullmatch(item_id) or USER_PRODUCT_ID.fullmatch(item_id)) or (selected_item and not ITEM_ID.fullmatch(selected_item)):
        raise MercadoLivreError('Identificador de anúncio inválido.')
    catalog_id = None
    item = None
    if USER_PRODUCT_ID.fullmatch(item_id):
        item_id, item = item_from_user_product(item_id, access_token)
    if catalog:
        catalog_id = item_id
        if not selected_item:
            raise MercadoLivreError('Selecione uma publicação MLB específica no link de catálogo (wid ou item_id).')
        candidate = request_json(f'{API_ROOT}/items/{selected_item}', access_token)
        if candidate.get('catalog_product_id') != catalog_id:
            raise MercadoLivreError('O anúncio selecionado não pertence ao produto de catálogo informado.')
        item_id, item = selected_item, candidate
    item = item or request_json(f'{API_ROOT}/items/{item_id}', access_token)
    if not isinstance(item, dict) or item.get('id') != item_id:
        raise MercadoLivreError('A API retornou um anúncio diferente ou inválido.')
    catalog_id = catalog_id or item.get('catalog_product_id')
    statuses = {'item': {'state': 'available', 'httpStatus': 200}}
    try:
        description = request_json(f'{API_ROOT}/items/{item_id}/description', access_token)
    except MercadoLivreError as error:
        if error.status == 401:
            raise
        statuses['description'] = resource_error(error)
        description = {}
    try:
        query = {'limit': 10}
        if isinstance(catalog_id, str) and ITEM_ID.fullmatch(catalog_id):
            query['catalog_product_id'] = catalog_id
        opinions = request_json(
            f'{API_ROOT}/reviews/item/{item_id}?{urllib.parse.urlencode(query)}',
            access_token,
        )
    except MercadoLivreError as error:
        if error.status == 401:
            raise
        statuses['reviews'] = resource_error(error)
        opinions = {}
    if not isinstance(description, dict):
        statuses['description'] = {'state': 'error', 'httpStatus': 200}
        description = {}
    if not isinstance(opinions, dict):
        statuses['reviews'] = {'state': 'error', 'httpStatus': 200}
        opinions = {}
    # Bounded pagination uses the documented offset/limit; a later failure keeps earlier pages.
    if isinstance(opinions.get('reviews'), list):
        for page_index in range(1, max(1, min(10, review_pages))):
            if len(opinions['reviews']) < page_index * 10:
                break
            try:
                next_page = request_json(f'{API_ROOT}/reviews/item/{item_id}?{urllib.parse.urlencode({**query, "offset": page_index * 10})}', access_token)
                entries = next_page.get('reviews') if isinstance(next_page, dict) else None
                if not isinstance(entries, list):
                    break
                opinions['reviews'].extend(entries)
            except MercadoLivreError as error:
                if error.status == 401:
                    raise
                statuses['reviews'] = {**resource_error(error), 'partial': True}
                break
    product = product_from_api(item, description, opinions)
    for key, value in [('description', description.get('plain_text')), ('reviews', opinions)]:
        statuses.setdefault(key, {'state': 'available' if value else 'absent', 'httpStatus': 200})
    statuses['images'] = {'state': 'available' if product['images'] else 'absent'}
    product['reviewsSummary'] = {'average': number(opinions.get('rating_average')), 'total': number((opinions.get('paging') or {}).get('total')) if isinstance(opinions.get('paging'), dict) else None, 'distribution': opinions.get('rating_levels'), 'sourceUrl': product['sourceUrl'], 'fetchedAt': product['fetchedAt']}
    product['sourceStatus'] = statuses
    product['importNotice'] = ' • '.join(f'{key}: {value["state"]}' for key, value in statuses.items() if value['state'] != 'available')
    return product


def resource_error(error):
    return {'state': 'forbidden' if error.status == 403 else 'absent' if error.status == 404 else 'transient_error' if error.status in (None, 429, 500, 502, 503, 504) else 'error', 'httpStatus': error.status}


def approved_media(url):
    if not isinstance(url, str):
        return False
    try:
        parsed = urllib.parse.urlsplit(url)
        return parsed.scheme == 'https' and (parsed.hostname or '').endswith('.mlstatic.com') and parsed.port in (None, 443) and not parsed.username and not parsed.password
    except ValueError:
        return False
