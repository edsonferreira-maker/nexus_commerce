"""Payment gateway contracts and the PixzyPay HTTP implementation."""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Callable


PIXZY_PROVIDER = "pixzy"
PAYMENT_STATUSES = frozenset({"pending", "paid", "expired", "failed", "cancelled"})


def parse_brl_cents(value: str) -> int:
    """Parse a displayed BRL price without using binary floating point."""
    if not isinstance(value, str):
        raise ValueError("Preço inválido.")
    normalized = re.sub(r"[^0-9,.]", "", value.strip())
    if not normalized:
        raise ValueError("Preço inválido.")
    if "," in normalized:
        whole, cents = normalized.rsplit(",", 1)
        if len(cents) != 2 or not cents.isdigit():
            raise ValueError("Preço inválido.")
        whole = whole.replace(".", "")
    else:
        whole, cents = normalized.replace(".", ""), "00"
    if not whole.isdigit():
        raise ValueError("Preço inválido.")
    return int(whole) * 100 + int(cents)


class PaymentGatewayError(Exception):
    """Safe, normalized provider error.

    ``details`` is intended for server-side diagnostics only. The token and
    authorization headers are deliberately never included.
    """

    def __init__(self, message: str, *, status: int = 502, details: str = "", retry_after: int | None = None):
        super().__init__(message)
        self.status = status
        self.details = details
        self.retry_after = retry_after


class PaymentGateway(ABC):
    @abstractmethod
    def create_payment(self, payload: dict) -> dict:
        raise NotImplementedError

    @abstractmethod
    def get_payment(self, transaction_id: str) -> dict:
        raise NotImplementedError

    @abstractmethod
    def get_account(self) -> dict:
        raise NotImplementedError

    def create_withdrawal(self, payload: dict) -> dict:
        """Provider capability prepared for future, explicitly not exposed."""
        raise NotImplementedError("Saques não estão habilitados no Nexo Commerce.")


@dataclass(frozen=True)
class PixzyConfig:
    api_token: str
    base_url: str = "https://app.pixzypay.com/api"
    webhook_url: str = ""
    timeout: float = 15.0

    @property
    def configured(self) -> bool:
        return bool(self.api_token and self.webhook_url)


class PixzyClient(PaymentGateway):
    """Small stdlib-only PixzyPay client with normalized failures and 429 retry."""

    def __init__(
        self,
        config: PixzyConfig,
        *,
        opener: Callable = urllib.request.urlopen,
        sleeper: Callable[[float], None] = time.sleep,
        max_rate_limit_retries: int = 1,
    ):
        self.config = config
        self._opener = opener
        self._sleep = sleeper
        self._max_rate_limit_retries = max_rate_limit_retries

    def create_payment(self, payload: dict) -> dict:
        return self._request("POST", "/transactions", payload)

    def get_payment(self, transaction_id: str) -> dict:
        encoded = urllib.parse.quote(transaction_id, safe="")
        return self._request("GET", f"/transactions/{encoded}")

    def get_account(self) -> dict:
        return self._request("GET", "/account")

    def create_withdrawal(self, payload: dict) -> dict:
        return self._request("POST", "/withdrawals", payload)

    def _request(self, method: str, path: str, payload: dict | None = None) -> dict:
        if not self.config.api_token:
            raise PaymentGatewayError("PixzyPay não configurada.", status=503)
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        url = self.config.base_url.rstrip("/") + path
        request = urllib.request.Request(
            url,
            data=data,
            method=method,
            headers={
                "Authorization": f"Bearer {self.config.api_token}",
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": "NexoCommerce/1.0",
            },
        )
        attempts = 0
        while True:
            try:
                with self._opener(request, timeout=self.config.timeout) as response:
                    body = self._decode(response.read())
                    return self._unwrap(body)
            except urllib.error.HTTPError as error:
                body = self._decode(error.read())
                retry_after = self._retry_after(error.headers.get("Retry-After"))
                if error.code == 429 and attempts < self._max_rate_limit_retries:
                    attempts += 1
                    self._sleep(float(retry_after or min(2**attempts, 30)))
                    continue
                raise self._http_error(error.code, body, retry_after) from None
            except (urllib.error.URLError, TimeoutError, OSError) as error:
                raise PaymentGatewayError(
                    "Gateway de pagamento temporariamente indisponível.",
                    status=503,
                    details=type(error).__name__,
                ) from None

    @staticmethod
    def _decode(raw: bytes) -> dict:
        try:
            value = json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return {}
        return value if isinstance(value, dict) else {}

    @staticmethod
    def _unwrap(body: dict) -> dict:
        data = body.get("data", body)
        if not isinstance(data, dict):
            raise PaymentGatewayError("Resposta inválida do gateway.", details="data is not an object")
        return data

    @staticmethod
    def _retry_after(value: str | None) -> int | None:
        try:
            parsed = int(value or "")
            return max(parsed, 0)
        except ValueError:
            return None

    @classmethod
    def _http_error(cls, status: int, body: dict, retry_after: int | None) -> PaymentGatewayError:
        raw_details = body.get("errors", body.get("error", ""))
        if isinstance(raw_details, dict):
            details = "; ".join(
                f"{field}: {', '.join(map(str, messages if isinstance(messages, list) else [messages]))}"
                for field, messages in raw_details.items()
            )
        else:
            details = str(raw_details or "")
        messages = {
            401: "Credencial da PixzyPay inválida.",
            403: "Conta PixzyPay sem permissão ou inativa.",
            404: "Pagamento não encontrado no gateway.",
            422: "Dados do pagamento rejeitados pelo gateway.",
            429: "Limite de consultas da PixzyPay atingido.",
            500: "A PixzyPay não conseguiu processar a solicitação.",
            503: "Gateway de pagamento temporariamente indisponível.",
        }
        return PaymentGatewayError(
            messages.get(status, "Não foi possível comunicar com o gateway."),
            status=status,
            details=details,
            retry_after=retry_after,
        )
