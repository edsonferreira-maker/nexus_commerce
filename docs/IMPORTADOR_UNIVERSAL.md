# Importador universal — implementação e operação

O projeto continua em Flutter, Python (HTTPServer) e SQLite. O ZIP contém requisitos, preservados em `AGENTS.md` e `PROMPT_CODEX.md`; não é usado como código de um site pronto.

## Fluxo disponível

1. Entre no painel. Em **Configurações → Central de Integrações**, configure a conexão da sua conta.
2. Mercado Livre: salve Client ID, Client Secret, URL de retorno exata e escopos; marque PKCE somente se habilitado no aplicativo. A URL termina em `/api/integrations/mercadolivre/callback`. Clique em **Autorizar conta**, conclua o consentimento e **Teste a conexão**.
3. Loja autorizada: cadastre os hosts HTTPS exatos da loja e de mídia; confirme a autorização de leitura. Configure API key Bearer opcional, região, ambiente, cabeçalhos não sensíveis e seletores simples. Teste usando uma URL de produto que tenha direito de acessar.
4. Abra **Importar produto**. Cole a URL ou MLB. Revise dados, diagnóstico por campo, imagens, descrição e avaliações. Edite à esquerda e acompanhe à direita. No mobile, use **Dados / Prévia**. A prévia usa o mesmo renderizador da publicação em iframe `sandbox=""`.
5. Se não houver uma integração acessível, selecione **Dados próprios / preenchimento manual**. HTML fornecido manualmente exige confirmação de autorização e não executa scripts. HTML do Mercado Livre é recusado; dados próprios podem ser preenchidos manualmente.
6. Envie fotos próprias quando necessário. Imagens externas precisam pertencer à allowlist de mídia; configure o CDN do proprietário. URLs não são fabricadas e imagens não são copiadas da fonte. Falhas têm indicação explícita para consultar a origem.
7. Cadastre produtos próprios no formulário recolhível. Selecione recomendações e ordene seus slugs. Produtos externos ou de outras organizações não podem ser incluídos como recomendações próprias.
8. Confirme os direitos de publicação e publique. A página pública permanente `/produto/{slug}` também responde em `/p/{slug}`, com HTML completo sem exigir JavaScript ou extensão. **Editar sua página** restaura os dados da publicação e mantém a URL.

As páginas do importador universal vinculam à oferta original, sem reproduzir checkout, login, marca ou identidade da loja externa. Os pagamentos PixzyPay existentes continuam restritos à administração original; não são compartilhados com novas contas.

## Adaptadores e extração

`SourceAdapter` define `canHandle`, `resolve`, `extract`, `capabilities` e `diagnose`. `MercadoLivreAdapter` usa os recursos oficiais. `GenericAdapter` lê JSON-LD Product/Offer/AggregateRating/Review/hasVariant, OpenGraph, microdata, HTML semântico, lazy images/srcset e seletores configuráveis. Detecta marketplaces conhecidos e sinais de Shopify/WooCommerce, mantendo o host como fonte.

A biblioteca padrão Python `HTMLParser` já estava presente e foi reaproveitada para uma árvore DOM inerte e seletores simples (tag, `#id`, `.classe`, `[itemprop="campo"]`). Não se adicionou uma dependência de scraping. Não há execução de JavaScript da loja nem fallback automático para Playwright. O frontend não tenta ler DOM cross-origin: leitura automática ocorre no backend e apenas para hosts explicitamente autorizados. Sites que dependem de login/JavaScript/anti-bot podem exigir integração oficial, HTML autorizado ou dados próprios.

A chave opcional da conexão genérica é Bearer enviado à URL original da loja. Não é um adaptador arbitrário de API JSON; APIs de plataformas que exijam assinatura, endpoints específicos ou contratos próprios precisam de um novo adaptador documentado. Nenhuma chave elimina limitações de acesso a produtos de terceiros.

Cada campo carrega `value`, `source`, `selector_or_path`, `retrieved_at`, `confidence`, `status`, `error`. Campos ausentes permanecem vazios, notas não são criadas e reputação de vendedor não vira avaliação do produto. As avaliações importadas podem ser removidas, não editadas ou inventadas. O histórico de importação é retido por até 30 dias, com proveniência e autorização do operador.

## Contas, RBAC e persistência

A administração original pode criar usuários na Central. Organização vazia cria um tenant novo; o identificador retornado permite cadastrar colaboradores na mesma organização. Perfis: proprietário gerencia conexões; editor importa/publica; leitor apenas consulta. Não há cadastro público. Senhas adicionais usam scrypt; sessões opacas ficam em cookies HttpOnly, SameSite=Lax (necessário para retorno OAuth), Secure quando HTTPS. O backend verifica Origin nas mutações e exige JSON nas APIs administrativas.

Na inicialização são criadas idempotentemente as tabelas `account_users`, `account_sessions`, `tenant_connections`, `integration_audit`, `tenant_oauth`, `tenant_records`. Não há remoção de produtos antigos: o tenant `admin` conserva as tabelas originais. Novas organizações usam chave composta `(tenant, kind, key)` e slugs derivados de tenant + identidade da fonte. Catálogo, rascunhos, cache, estados OAuth e conexões são isolados. Slugs de páginas já publicadas são públicos por definição.

Segredos usam Fernet (criptografia autenticada) no servidor. A API de listagem retorna apenas configuração não secreta e indicação mascarada. Credenciais antigas do Mercado Livre e PixzyPay são migradas de texto puro para criptografia quando lidas. Configure `NEXO_CREDENTIAL_KEY` fora do banco em produção; o fallback local é `oauth.key` privado (0600) fora do SQLite. Preserve a chave para restaurar backups. Trocar a chave mestra sem recriptografar os registros torna as credenciais anteriores ilegíveis.

OAuth verifica state, conta iniciadora, expiração e uso único; suporta PKCE S256. Revogar/editar a configuração invalida o fluxo pendente. Refresh usa lock de arquivo por tenant/provedor, relê o token sob lock e grava access/refresh rotacionados atomicamente. Auditoria guarda ator, provedor, ação e horário, sem valores secretos; consulta autenticada em `/api/integrations/audit`. Rotacionar credenciais usa **Salvar / rotacionar**; secret vazio preserva o valor atual. Revogação remove credenciais locais: revogue também a autorização no provedor.

Cache Mercado Livre usa `ML_CACHE_TTL` e `revalidate=true`, com invalidação por revisão da conexão; não serve rascunhos de outra conta. HTML é sempre reprocessado. Preços importados antigos são sinalizados na página SSR; preços fornecidos pelo operador são identificados como `user_supplied`.

## Rede e mídia

URLs externas: HTTPS, porta 443, sem userinfo, IP literal, host local, curinga ou host semelhante. Na conexão genérica, todos os IPs resolvidos são conferidos e a conexão TLS é fixada ao IP validado, mantendo SNI/Host e verificação do certificado. Redirecionamentos limitados são revalidados; Bearer não segue para outro host. Endereços privados, loopback, link-local, IPv4 mapeado e túneis de tradução IPv6 são bloqueados. Timeout de rede de 12s e tamanho máximo de HTML de 8 MiB. Não há tentativa de contornar bloqueios ou CAPTCHA.

Mercado Livre tem host de API fixo, sem redirecionamentos, timeout, tratamento de 400/401/403/404/429/5xx e retries limitados de GET com backoff/jitter/Retry-After. Grants rotativos não são repetidos automaticamente. Imports e testes de conexão têm limite por tenant; login tem limite por IP. Imagens passam por política de origem, textos são escapados e CSP restringe scripts, frames, mídia e formulários. O iframe de prévia não permite scripts ou navegação superior.

## Endpoints

- `GET /api/integrations`: provedores, status, última verificação e configuração pública da conta.
- `POST /api/integrations/{generic|mercadolivre}/{save|test|revoke}`: proprietário, sem devolução de segredos.
- `GET /api/integrations/mercadolivre/connect` e `/callback`: OAuth vinculado à conta.
- `POST /api/accounts`: cadastro administrativo de contas/organizações e perfis.
- `POST /api/products/import`: `url`, opcionais `html`, `manual`, `authorizationConfirmed`, `revalidate`.
- `GET /api/products/draft?id=...`: dados editáveis da própria conta.
- `POST /api/products/preview`: `id`, `page`, `recommendations`; retorna HTML escapado sem salvar/publicar.
- `POST /api/products/publish`: rascunho próprio, alterações validadas, recomendações próprias, `rightsConfirmed=true`.
- `GET /api/products`: catálogo da conta. `/produto/{slug}`: página pública SSR.

## Execução e testes

```sh
flutter pub get
python3 scripts/run_local.py
# Abra o endereço local informado pelo inicializador.
```

Use `.env.example` como referência; não sobrescreva um `.env` existente. O inicializador instala dependências Python, mas não baixa navegador para importar produtos. Docker: `docker compose up -d --build`.

```sh
.local/venv/bin/python -m unittest discover -s server -p 'test_*.py'
flutter analyze
flutter test
flutter build web --release --dart-define=NEXO_SELF_HOSTED=true

# Somente para testes do nosso site:
PLAYWRIGHT_BROWSERS_PATH=.local/browsers .local/venv/bin/python -m playwright install chromium firefox webkit
PLAYWRIGHT_BROWSERS_PATH=.local/browsers .local/venv/bin/python server/e2e_marketplace.py
PLAYWRIGHT_BROWSERS_PATH=.local/browsers .local/venv/bin/python server/e2e_universal.py

# Diagnóstico real sem imprimir tokens; forneça apenas um MLB autorizado:
.local/venv/bin/python server/diagnose_marketplace.py --item MLB_DO_ANUNCIO_AUTORIZADO
```

O diagnóstico existente consulta a conexão legada administrativa. Conexões da Central são testadas pela própria conta em **Testar conexão** e usadas pelo importador. Não forneça segredos no chat.

## Referências oficiais consultadas em 11/10/2026

- [OAuth e PKCE](https://developers.mercadolivre.com.br/autenticacao-e-autorizacao)
- [Descrição independente do item](https://developers.mercadolivre.com.br/pt_br/descricao-de-produtos)
- [Avaliações e restrições de acesso](https://developers.mercadolivre.com.br/pt_br/opinioes-sobre-um-produto)
- [User Products e relação com publicações](https://developers.mercadolivre.com.br/pt_br/user-products)

Não há consultas em lote neste fluxo; a migração de endpoints bulk não se aplica às leituras individuais usadas.
