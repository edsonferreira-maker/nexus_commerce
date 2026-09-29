"""Mercado Livre OAuth and product reads through the official API."""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime


API_ROOT = 'https://api.mercadolibre.com'
ITEM_ID = re.compile(r'MLB[-_]?([0-9]{7,15})', re.IGNORECASE)
USER_PRODUCT_ID = re.compile(r'MLBU[-_]?([0-9]{7,15})', re.IGNORECASE)


class MercadoLivreError(Exception):
    pass


def item_id_from_url(value: str) -> str:
    """Only accept Mercado Livre Brazil URLs; never fetch an arbitrary user URL."""
    parsed = urllib.parse.urlsplit(value.strip())
    hostname = (parsed.hostname or '').lower()
    if parsed.scheme not in ('http', 'https') or not (
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
        item = (urllib.parse.parse_qs(section).get('wid') or [''])[0]
        if ITEM_ID.fullmatch(item):
            return item.upper().replace('-', '').replace('_', '')
    return None


def request_json(url: str, token: str | None = None, form: dict | None = None):
    headers = {'Accept': 'application/json', 'User-Agent': 'NexoCommerce/1.0'}
    if token:
        headers['Authorization'] = f'Bearer {token}'
    body = None
    if form is not None:
        body = urllib.parse.urlencode(form).encode()
        headers['Content-Type'] = 'application/x-www-form-urlencoded'
    request = urllib.request.Request(url, data=body, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        details = {}
        try:
            details = json.loads(error.read().decode('utf-8', 'replace'))
        except (ValueError, UnicodeError):
            pass
        api_error = str(details.get('error') or '')
        api_message = str(details.get('message') or details.get('error_description') or '')
        if error.code == 400 and api_error == 'invalid_grant':
            raise MercadoLivreError(
                'O código de autorização expirou ou já foi utilizado. Conecte a conta novamente.'
            ) from error
        if error.code in (400, 401) and api_error == 'invalid_client':
            raise MercadoLivreError(
                'O Mercado Livre recusou as credenciais do aplicativo.'
            ) from error
        if error.code == 401:
            raise MercadoLivreError('A autorização do Mercado Livre expirou. Reconecte a conta.') from error
        if error.code == 404:
            raise MercadoLivreError('Produto não encontrado no Mercado Livre.') from error
        if error.code == 429:
            raise MercadoLivreError('O Mercado Livre limitou as consultas. Tente novamente em alguns minutos.') from error
        if error.code == 403 and str(details.get('code') or '') == 'PA_UNAUTHORIZED_RESULT_FROM_POLICIES':
            raise MercadoLivreError(
                'A conta conectada não tem permissão para ler este anúncio. '
                'Habilite a permissão de leitura no aplicativo do Mercado Livre e conecte novamente.'
            ) from error
        if api_message and len(api_message) <= 240:
            raise MercadoLivreError(f'Mercado Livre: {api_message}') from error
        raise MercadoLivreError(f'Falha na API do Mercado Livre (HTTP {error.code}).') from error
    except (OSError, ValueError, json.JSONDecodeError) as error:
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
    return request_json(f'{API_ROOT}/oauth/token', form={
        'grant_type': 'refresh_token',
        'client_id': client_id,
        'client_secret': client_secret,
        'refresh_token': token,
    })


def money_brl(value) -> str:
    amount = float(value or 0)
    return 'R$ ' + f'{amount:,.2f}'.replace(',', '_').replace('.', ',').replace('_', '.')


def product_from_api(item: dict, description: dict, opinions: dict) -> dict:
    images = [
        picture.get('secure_url') or picture.get('url')
        for picture in item.get('pictures') or []
    ]
    images = [
        url for url in images
        if isinstance(url, str)
        and urllib.parse.urlsplit(url).scheme == 'https'
        and (urllib.parse.urlsplit(url).hostname or '').endswith('.mlstatic.com')
    ][:10]
    if not images:
        raise MercadoLivreError('O produto não tem fotos disponíveis para a prévia.')
    reviews = []
    for opinion in (opinions.get('reviews') or [])[:10]:
        if opinion.get('status') not in (None, 'published'):
            continue
        comment = opinion.get('content') or opinion.get('title') or ''
        if not isinstance(comment, str) or not comment.strip():
            continue
        created = opinion.get('date_created') or ''
        try:
            date = datetime.fromisoformat(created.replace('Z', '+00:00')).strftime('%d/%m/%Y')
        except ValueError:
            date = ''
        score = opinion.get('rate')
        reviews.append({
            'author': 'Comprador do Mercado Livre',
            'date': date,
            'comment': comment.strip(),
            'stars': score if isinstance(score, int) and 1 <= score <= 5 else 0,
        })
    rating = opinions.get('rating_average')
    rating_text = f'{float(rating):.1f}'.replace('.', ',') if isinstance(rating, (float, int)) else '0'
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
    if not ITEM_ID.fullmatch(item_id) or not link or link.scheme != 'https' or not (
        host == 'mercadolivre.com.br' or host.endswith('.mercadolivre.com.br')
        or host == 'mercadolibre.com.br' or host.endswith('.mercadolibre.com.br')
    ):
        raise MercadoLivreError('O Mercado Livre retornou dados incompletos para este anúncio.')
    return {
        'id': item_id,
        'slug': item_id.lower(),
        'name': str(item.get('title') or ''),
        'storeName': 'Minha loja',
        'price': money_brl(item.get('price')),
        'market': 'Mercado Livre',
        'sourceUrl': permalink,
        'images': images,
        'description': str(description.get('plain_text') or 'Descrição não fornecida pelo vendedor.'),
        'reviews': reviews,
        'rating': rating_text,
        'reviewCount': int((opinions.get('paging') or {}).get('total') or 0),
        'stock': int(item['available_quantity']) if item.get('available_quantity') is not None else -1,
        'brand': brand,
        'installmentText': '',
        'shippingText': 'Frete grátis no Mercado Livre' if (item.get('shipping') or {}).get('free_shipping') else '',
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
    fallback = None
    for candidate_id in candidates[:30]:
        try:
            candidate = request_json(f'{API_ROOT}/items/{candidate_id}', access_token)
        except MercadoLivreError:
            continue
        fallback = fallback or (candidate_id, candidate)
        if candidate.get('status') == 'active' and int(candidate.get('available_quantity') or 0) > 0:
            return candidate_id, candidate
    if fallback:
        return fallback
    raise MercadoLivreError('Não foi possível consultar os anúncios associados a este produto MLBU.')


def fetch_product(item_id: str, access_token: str, catalog: bool = False, selected_item: str | None = None) -> dict:
    catalog_id = None
    item = None
    if USER_PRODUCT_ID.fullmatch(item_id):
        item_id, item = item_from_user_product(item_id, access_token)
    if catalog:
        catalog_id = item_id
        if selected_item:
            try:
                candidate = request_json(f'{API_ROOT}/items/{selected_item}', access_token)
                if candidate.get('catalog_product_id') == catalog_id:
                    item_id, item = selected_item, candidate
            except MercadoLivreError:
                pass
        if item is None:
            catalog_data = request_json(f'{API_ROOT}/products/{catalog_id}', access_token)
            winner = (catalog_data.get('buy_box_winner') or {}).get('item_id')
            if not isinstance(winner, str) or not ITEM_ID.fullmatch(winner):
                raise MercadoLivreError('Este produto de catálogo não tem uma oferta disponível para importar.')
            item_id = winner
    item = item or request_json(f'{API_ROOT}/items/{item_id}', access_token)
    catalog_id = catalog_id or item.get('catalog_product_id')
    try:
        description = request_json(f'{API_ROOT}/items/{item_id}/description', access_token)
    except MercadoLivreError as error:
        if 'não encontrado' not in str(error):
            raise
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
        if 'não encontrado' not in str(error):
            raise
        opinions = {}
    return product_from_api(item, description, opinions)
