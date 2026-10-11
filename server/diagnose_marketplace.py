"""Print a credential-free API audit; never chooses a real listing automatically."""
import argparse
import json
import os
import sys
from pathlib import Path

from app import TrafficStore
from mercadolivre import API_ROOT, MercadoLivreError, ITEM_ID, request_json

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from run_local import load_env_file


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--item', help='MLB de um anúncio cujo acesso foi autorizado pelo operador')
    args = parser.parse_args()
    if args.item and not ITEM_ID.fullmatch(args.item):
        parser.error('Informe um identificador MLB válido.')
    load_env_file(ROOT / '.env')
    store = TrafficStore(Path(os.environ.get('NEXO_DATA_DIR', str(ROOT / '.local/data'))) / 'traffic.sqlite3')
    token = store.get_token()
    print(json.dumps({'configured': all(os.environ.get(k) for k in ('ML_CLIENT_ID', 'ML_CLIENT_SECRET', 'ML_REDIRECT_URI')), 'oauthConnected': bool(token), 'authorizedItemProvided': bool(args.item)}))
    resources = [('item', '/items/{item}'), ('description', '/items/{item}/description'), ('reviews', '/reviews/item/{item}?limit=10')]
    for resource, path in resources:
        result = {'resource': resource, 'endpoint': API_ROOT + path, 'headers': 'Accept: application/json; Authorization: Bearer [redacted]', 'httpStatus': None, 'fieldsPresent': []}
        if not token or not args.item:
            result['state'] = 'not_executed_no_oauth' if not token else 'not_executed_no_authorized_item'
        else:
            try:
                payload = request_json(API_ROOT + path.format(item=args.item), token['access_token'])
                result.update(httpStatus=200, state='available', fieldsPresent=sorted(payload.keys()))
            except MercadoLivreError as error:
                result.update(httpStatus=error.status, state='unavailable', reason=str(error))
        print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
