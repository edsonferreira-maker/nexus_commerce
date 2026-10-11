# Validação do importador universal — 11/10/2026

Implementação executada no repositório existente, preservando alterações locais anteriores. Foram lidos o `AGENTS.md`, o `PROMPT_CODEX.md` completo e o ADENDO PRIORITÁRIO do ZIP, além da skill `web-api-marketplace-specialist` local e sua versão ampliada no pacote.

## Diagnóstico e correções

Antes das mudanças, 61 testes Python passavam. Havia base de páginas SSR e integração Mercado Livre em andamento, mas credenciais OAuth e catálogo eram administrativos globais. O formulário SSR não era editor de duas colunas com preview em tempo real. O caminho genérico tentava captura por navegador sem cadastro explícito de domínio autorizado. O parser restringia fontes a alguns marketplaces e removia a query da identidade do produto, causando colisão entre anúncios que usam `?id=...`.

Foram implementados adaptadores universais, autorização por host, DNS/TLS fixado contra SSRF, credenciais próprias e criptografadas, identidades/organizações/RBAC, auditoria, PKCE e refresh concorrente, isolamento de rascunhos/catálogo/cache, consentimento de publicação em todas as fontes, editor com prévia segura e fiel ao SSR, edição de publicação existente, recomendações próprias, políticas de mídia e fixtures. Também foram corrigidos preço zero, NaN/Infinity, nota ausente, identificação de produtos por query e configuração de execução que instalava um navegador desnecessariamente.

Credenciais legadas Mercado Livre/PixzyPay são lidas com migração para criptografia. Não houve migração de framework, remoção de produtos existentes, publicação externa ou alteração do `.env` real.

## Resultados observados

| Verificação | Resultado |
|---|---|
| `python -m unittest discover -s server -p 'test_*.py'` | **79 testes passaram**; 21,573 s |
| `flutter analyze` | **Sem problemas** |
| `flutter test` | **9 testes passaram** |
| `flutter build web --release --dart-define=NEXO_SELF_HOSTED=true` | **Build gerado**, `build/web` |
| `python -m unittest discover -s server -p 'e2e_*.py'` | **2 fluxos completos passaram em Chromium, Firefox e WebKit**; 23,395 s |
| SSR/curl e HTML sem extensão | Conteúdo principal, imagens autorizadas, descrição, avaliações e recomendações presentes no HTML inicial |
| Segurança e dados | SSRF/DNS/redirects, credencial em repouso, ausência de segredo na UI/auditoria, RBAC, isolamento, CSRF/Origin, XSS, state único, PKCE, refresh rotativo concorrente, rotação/revogação e cache verificados |
| Parser universal | JSON-LD, OpenGraph, microdata, seletores, lazy/srcset, variações, reviews, HTML incompleto, moeda/preço, ausência de avaliações e URLs diferenciadas por query |
| Preview | Colunas no desktop antes/depois da importação, iframe sandbox sem scripts, edição incremental, mobile por abas, publicação/reabertura e imagem quebrada com fallback |
| `git diff --check` | **Sem erros** |

Os E2E operam somente no nosso servidor local. APIs, loja HTML e CDN usam fixtures explícitas: não comprovam acesso de produção nem direitos sobre conteúdos de terceiros. O conteúdo público foi verificado com JavaScript desativado; no Firefox a restrição foi feita com `script-src 'none'` por uma limitação do Playwright ao avaliar layout com JavaScript desativado na configuração do contexto.

Logs locais: [validação](../.local/validation-universal/). Screenshots/HTML: [fluxo universal](../.local/universal-evidence/) e [fluxo Mercado Livre](../.local/marketplace-evidence/). Não há tokens reais nos artefatos.

## Conexões reais e permissões pendentes

O comando `.local/venv/bin/python server/diagnose_marketplace.py` carregou o `.env` existente e consultou apenas a presença das configurações e do token, sem imprimir seus valores. Resultado: **aplicativo configurado, nenhuma conta OAuth conectada e nenhum ITEM_ID autorizado fornecido**. Não foram criados tokens ou escolhidos anúncios reais arbitrariamente.

| Recurso real | Endpoint HTTPS | Header/autenticação | HTTP observado / campos presentes | Impacto |
|---|---|---|---|---|
| Produto | `api.mercadolibre.com/items/{ITEM_ID}` | Accept JSON; Bearer da conta | **Não consultado**: sem OAuth/ITEM_ID autorizado | Nenhum título/preço/foto real confirmado |
| Descrição | `api.mercadolibre.com/items/{ITEM_ID}/description` | Accept JSON; Bearer da conta | **Não consultado**: mesmo bloqueio | Descrição real não confirmada |
| Avaliações | `api.mercadolibre.com/reviews/item/{ITEM_ID}?limit=10` | Accept JSON; Bearer da conta | **Não consultado**: mesmo bloqueio | Notas/reviews reais não confirmados |
| Teste OAuth da Central | `api.mercadolibre.com/users/me` | Bearer próprio, backend | Implementado e testado com fixture; **pendente de autorização real** | A UI permite verificar a conta após o consentimento |
| Loja genérica | URL HTTPS em allowlist da conta | Bearer opcional, backend | Fixtures HTTP 200 e erros 401/403/404/429/500; **sem domínio real autorizado fornecido** | Não há alegação de extração real de loja de terceiros |

O próximo passo operacional é conectar a conta pela Central e importar um anúncio cujo acesso foi autorizado; para loja própria, cadastrar os domínios e executar o teste com uma URL real. Não envie segredos pelo chat. Uma conta conectada não garante permissão sobre todos os produtos, fotos ou avaliações. Bloqueios são exibidos, sem tentativa de contorno. A revogação local deve ser complementada pela revogação no provedor quando aplicável.

## Arquivos da implementação

- `server/universal_import.py`: contrato de adaptadores, DOM inerte, diagnóstico, rede segura e mídia.
- `server/integrations.py`: migrações idempotentes, contas/sessões, segredos, OAuth/PKCE, locks, auditoria e repositório por organização.
- `server/app.py`: rotas autenticadas, RBAC, importação, preview/publicação, compatibilidade administrativa e criptografia de credenciais legadas.
- `server/product_pages.py`: editor lateral, Central, iframe, renderer público e políticas de exibição.
- `server/page_import.py`: parser reutilizado, fontes variadas, correções de preços/notas/identidade, desativação de captura automática por navegador.
- `lib/nexo_app.dart`: acesso ao editor universal e à Central no painel Flutter.
- `server/test_universal.py`, `server/fixtures/authorized_product.html`, `server/e2e_universal.py`: testes novos.
- `server/test_app.py`, `server/test_page_import.py`, `server/test_marketplace_contract.py`: contratos ajustados para autorização explícita e adaptação do importador.
- `.env.example`, `compose.yaml`, `Dockerfile`, `scripts/run_local.py`: chave externa e execução sem navegador obrigatório.
- `README.md`, `docs/IMPORTADOR_UNIVERSAL.md`, `AGENTS.md`, `PROMPT_CODEX.md`: operação, requisitos e fontes.

Outros arquivos que já estavam modificados ao iniciar foram preservados. Como executar e testar: [guia operacional](IMPORTADOR_UNIVERSAL.md).

## Exemplo sanitizado do modelo

Exemplo produzido **da fixture autorizada de teste**, não de uma consulta real. A aplicação mantém os nomes do contrato existente (`name`, `images`, `reviews`) e acrescenta metadados/diagnóstico:

```json
{
  "source": "generic",
  "market": "shop.example",
  "sourceUrl": "https://shop.example/lamp",
  "name": "Luminária autorizada",
  "price": "R$ 89,90",
  "currency": "BRL",
  "images": ["https://cdn.shop.example/lamp.jpg"],
  "description": "Descrição fornecida pela fixture",
  "rating": "4,5",
  "reviewCount": 2,
  "variations": ["Branco", "Preto"],
  "fieldDiagnostics": {
    "name": {
      "value": "Luminária autorizada",
      "source": "authorized_html",
      "selector_or_path": "jsonld.Product.name",
      "retrieved_at": "2026-10-11T00:00:00+00:00",
      "confidence": 0.9,
      "status": "available",
      "error": null
    }
  }
}
```

O exemplo foi abreviado e o horário generalizado; galeria completa, comentários e proveniência por campo estão nos testes e no rascunho. Campos ausentes não recebem valores fictícios. Referências oficiais consultadas estão no [guia](IMPORTADOR_UNIVERSAL.md#referências-oficiais-consultadas-em-11102026).
