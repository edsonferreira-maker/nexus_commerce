import io
import json
import urllib.error
import unittest

from payments import PaymentGatewayError, PixzyClient, PixzyConfig, parse_brl_cents


class FakeResponse:
    def __init__(self, payload, status=200, headers=None):
        self.payload = payload
        self.status = status
        self.headers = headers or {}

    def read(self):
        return json.dumps(self.payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class PixzyClientTest(unittest.TestCase):
    def test_parses_brl_without_float(self):
        self.assertEqual(parse_brl_cents('R$ 1.234,56'), 123456)
        self.assertEqual(parse_brl_cents('R$ 5,00'), 500)
        with self.assertRaises(ValueError):
            parse_brl_cents('R$ 10,9')

    def test_unwraps_create_response_and_sends_bearer(self):
        seen = {}

        def opener(request, timeout):
            seen['authorization'] = request.headers['Authorization']
            seen['timeout'] = timeout
            return FakeResponse({'status': 'success', 'data': {'transaction_id': 'tx-1'}})

        client = PixzyClient(PixzyConfig('secret'), opener=opener)
        result = client.create_payment({'amount': 500})
        self.assertEqual(result['transaction_id'], 'tx-1')
        self.assertEqual(seen['authorization'], 'Bearer secret')
        self.assertNotIn('secret', repr(result))

    def test_normalizes_documented_http_errors(self):
        cases = {
            401: ({'error': 'Unauthenticated'}, 'Credencial'),
            403: ({'error': 'Account not active'}, 'sem permissão'),
            404: ({'error': 'Not found'}, 'não encontrado'),
            422: ({'errors': 'amount obrigatório'}, 'rejeitados'),
            500: ({'error': 'Internal'}, 'não conseguiu'),
            503: ({'error': 'Unavailable'}, 'indisponível'),
        }
        for status, (payload, expected) in cases.items():
            with self.subTest(status=status):
                def opener(_request, timeout, code=status, body=payload):
                    raise urllib.error.HTTPError(
                        'https://example.test', code, 'error', {}, io.BytesIO(json.dumps(body).encode()),
                    )

                with self.assertRaises(PaymentGatewayError) as failure:
                    PixzyClient(PixzyConfig('secret'), opener=opener).get_payment('tx')
                self.assertEqual(failure.exception.status, status)
                self.assertIn(expected, str(failure.exception))

    def test_parses_object_validation_error(self):
        def opener(_request, timeout):
            raise urllib.error.HTTPError(
                'https://example.test', 422, 'error', {},
                io.BytesIO(b'{"errors":{"amount":["obrigatorio"],"pix_key":["invalida"]}}'),
            )

        with self.assertRaises(PaymentGatewayError) as failure:
            PixzyClient(PixzyConfig('secret'), opener=opener).create_payment({})
        self.assertIn('amount: obrigatorio', failure.exception.details)
        self.assertIn('pix_key: invalida', failure.exception.details)

    def test_retries_429_once_using_retry_after(self):
        calls = []
        sleeps = []

        def opener(_request, timeout):
            calls.append(timeout)
            if len(calls) == 1:
                raise urllib.error.HTTPError(
                    'https://example.test', 429, 'rate', {'Retry-After': '3'}, io.BytesIO(b'{}'),
                )
            return FakeResponse({'data': {'id': 'tx'}})

        result = PixzyClient(PixzyConfig('secret'), opener=opener, sleeper=sleeps.append).get_payment('tx')
        self.assertEqual(result['id'], 'tx')
        self.assertEqual(sleeps, [3.0])

    def test_exposes_retry_after_when_429_persists(self):
        def opener(_request, timeout):
            raise urllib.error.HTTPError(
                'https://example.test', 429, 'rate', {'Retry-After': '7'}, io.BytesIO(b'{}'),
            )

        with self.assertRaises(PaymentGatewayError) as failure:
            PixzyClient(
                PixzyConfig('secret'), opener=opener, max_rate_limit_retries=0,
            ).get_payment('tx')
        self.assertEqual(failure.exception.status, 429)
        self.assertEqual(failure.exception.retry_after, 7)


if __name__ == '__main__':
    unittest.main()
