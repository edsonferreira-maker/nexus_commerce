# PROMPT PARA CODEX — Importador de produtos Mercado Livre e páginas próprias

Você é o engenheiro principal full-stack deste repositório, com especialização em aplicações web, SSR, integrações REST, OAuth2, APIs do Mercado Livre, arquitetura de dados, segurança, depuração e testes. Use obrigatoriamente a skill local `.agents/skills/web-api-marketplace-specialist/SKILL.md` e as regras do `AGENTS.md`.

## 1. Objetivo de negócio

O usuário informa o endereço de uma publicação de produto do Mercado Livre. O sistema extrai, pelas APIs e permissões oficialmente disponíveis, os dados do anúncio e gera um novo endereço no NOSSO domínio, que exibe uma página de produto em IDENTIDADE VISUAL PRÓPRIA e recomendações que vêm EXCLUSIVAMENTE do nosso catálogo. Nenhum visitante precisa instalar extensão ou plugin. A página deve ter HTML útil gerado no servidor (SSR/SSG), permitindo usar "Exibir código-fonte" e "Inspecionar" no Chrome, Safari, Firefox ou Edge.

Não copie o site original nem se passe pelo Mercado Livre. A página deve explicar a origem dos dados, mostrar vínculo claro para o anúncio original quando aplicável e distinguir avaliações do Mercado Livre das recomendações da nossa plataforma. Nunca replique tela de login, carrinho ou checkout do marketplace. Use dados, logos, fotos, avaliações e textos SOMENTE quando os direitos, termos e permissões aplicáveis autorizarem; caso contrário, solicite fonte autorizada, dados próprios ou omita o campo. É proibido raspar o Mercado Livre por Playwright, Puppeteer, browser automation ou requisições ao HTML em desacordo com seus termos, ou contornar CAPTCHA/anti-bot/limitações técnicas.

## 2. Primeira tarefa: diagnóstico do projeto existente

ANTES DE ALTERAR CÓDIGO:
1. Examine estrutura, frameworks, autenticação, banco, rotas, variáveis `.env.example`, integrações existentes, logs e testes. Reuse as tecnologias existentes e não migre frameworks sem necessidade.
2. Localize toda chamada ao Mercado Livre e produza tabela `recurso -> endpoint -> headers -> autenticação -> HTTP status -> campos presentes -> impacto na tela` (nunca imprima tokens).
3. Reproduza falhas de imagem, descrição e avaliação com um `ITEM_ID` permitido fornecido no projeto. Não invente IDs, credenciais nem resultados.
4. Diferencie `item_id` (publicação) de `catalog_product_id` (produto de catálogo); alguns links `/p/MLB...` não identificam diretamente a publicação. Se não houver um item determinado com segurança, apresente seleção/erro claro, sem adivinhar anúncio e vendedor.
5. Mostre o que já funciona e o que precisa de permissão/aprovação, antes de alterar a integração.

## 3. Fluxo de importação

Entrada: URL pública do Mercado Livre ou `item_id` válido. Validar o domínio, protocolos HTTPS, redirecionamentos e formatos; prevenir SSRF, inclusive redirecionamentos, endereços privados, portas inesperadas e hosts parecidos. NÃO buscar HTML da página externa como mecanismo de extração.

Use recursos oficialmente documentados, verificados na data de implementação:
- `GET https://api.mercadolibre.com/items/{ITEM_ID}`: título, preço, moeda, permalink, identificadores, fotos, atributos, variações, modalidade de envio e campos públicos disponíveis.
- `GET https://api.mercadolibre.com/items/{ITEM_ID}/description`: descrição em `plain_text` (não assumir que venha no GET principal).
- `GET https://api.mercadolibre.com/reviews/item/{ITEM_ID}`: avaliação média, distribuição de estrelas, quantidade, comentários e mídias permitidas, paginando quando habilitado.
- Para catálogo/variações: consultar APENAS rotas oficiais atuais cuja permissão e documentação sejam confirmadas. Mapear `variations[].picture_ids` às entradas de `pictures[]`; não assumir que ID de catálogo seja um item.
- Consultas em lote, quando necessárias: preferir os novos endpoints `/items/bulk?ids=...`, conforme cronograma de migração oficial de 2026.

Consultar documentação oficial, e não inventar endpoint ou campo. Algumas operações precisam do token correto e alguns campos não são acessíveis a contas de terceiros. Testar HTTP 200/400/401/403/404/429 e formatos inesperados.

## 4. OAuth2 e segurança

- Consumir a API SOMENTE pelo servidor, com Bearer token em header. Nunca expor `client_secret`, `access_token`, `refresh_token` no frontend, HTML, logs ou query string.
- Implementar fluxo OAuth2 Authorization Code, storage seguro/criptografado, refresh automático e atualização ATÔMICA do refresh token rotacionado; evitar refresh simultâneo por múltiplas requisições.
- Reautorizar quando necessário e mostrar mensagens específicas ao operador para token expirado, recurso proibido ou item inacessível.
- Implementar timeout, retentativas limitadas e backoff exponencial com jitter nos erros transitórios, respeitando `Retry-After` em 429. Nunca tentar burlar bloqueios.
- Validar entradas com esquema tipado (ex.: Zod, se a base tecnológica permitir); sanitizar textos e HTML, evitar XSS, CSRF e SSRF, restringir mídia a origens aprovadas, aplicar rate limiting no importador e proteger rotas administrativas.
- Isolar metadados/licenças/proveniência; não re-hospedar imagens nem conteúdo protegido sem direitos explícitos. Só mostrar mídias quando permitido e tecnicamente acessível.

## 5. Normalização e persistência

Definir modelo interno estável, com dados opcionais explícitos, por exemplo:

```
ProductViewModel {
  source: 'mercadolivre'; itemId; catalogProductId?; sourceUrl;
  title; price?; currency?; originalPrice?;
  images: [{url, pictureId?, variationId?, order, alt?, provenance}];
  description?: {plainText, provenance};
  attributes: [{id, name, value}];
  variations: [{id, attributes, pictureIds}];
  reviews?: {average?, total?, distribution?, entries?:[], sourceUrl?, fetchedAt?};
  shipping?: object; seller?: object;
  missingFields: string[]; fetchedAt; sourceStatus;
}
```

Não inventar preço, estrelas, avaliações, estoque, promoção ou frete. Distinguir ausência de autorização de inexistência de dado. Exibir estados: disponível, ausente, sem permissão, desatualizado, erro transitório. Não misturar avaliação de produto com reputação do vendedor.

Persistir mapeamento `sourceUrl -> itemId/catalogProductId -> nosso slug`, histórico mínimo, data da sincronização e lista de produtos recomendados administrada por nós. Criar cache com TTL configurável e invalidação; manter preço/frete indisponíveis ou sinalizados quando não for possível garantir atualização.

## 6. Interface web e HTML inspecionável

- Criar formulário administrativo para colar URL, validar, importar, visualizar dados, selecionar produtos recomendados e publicar.
- Gerar rota pública permanente e slug único, como `/produto/slug-legivel`.
- Renderizar HTML sem dependência obrigatória de JavaScript cliente para o conteúdo central (SSR/SSG, de acordo com a arquitetura existente); verificar a resposta de `curl` e o recurso "Exibir código-fonte".
- Criar layout original, responsivo, rápido e acessível com título, galeria de fotos permitidas, preço, descrição, atributos, avaliação quando disponível e seções de recomendações do NOSSO catálogo.
- Diferenciar visual e textualmente "Informações do anúncio original" e "Recomendações da nossa loja"; incluir atribuição e link para fonte de maneira adequada.
- Imagens: usar `pictures[].secure_url` quando fornecido e permitido; considerar `secure_thumbnail` como recurso de fallback. Preservar URLs oficiais retornadas sem fabricar tamanhos ou modificar identificadores CDN. Mostrar fallback explícito quando imagem estiver bloqueada ou indisponível; incluir `alt`, lazy loading, dimensões quando conhecidas e testes de carregamento.
- Criar metatags SEO/Open Graph com dados autorizados, acessibilidade via teclado e estados de erro/amostra.
- Não interceptar credenciais de usuários do marketplace nem insinuar compra feita nele quando o usuário estiver no nosso domínio.

## 7. Integração das recomendações próprias

Definir interface `RecommendationsRepository` ou equivalente integrada ao banco/API atual de produtos próprios. As listas devem ser controladas pelo operador, com ordem, inclusão e remoção; nunca reaproveitar os produtos recomendados visualizados no Mercado Livre. Renderização pode existir com zero itens. Garantir que os links levem aos destinos verdadeiros com identificação clara do vendedor.

## 8. Entregas e validação obrigatória

Implementar de ponta a ponta o que for possível COM ACESSO REAL e credenciais já disponíveis, sem pedir segredos no chat ou criar tokens fictícios. Se não houver credenciais ou permissões, implementar adaptador mock para testes e relatório do bloqueio sem fingir êxito na produção.

Testes:
- Unitários: parsing da URL, distinção item/catalogo, galeria/variações, normalização, campos opcionais, tratamento de erro.
- Integração com respostas simuladas: `/items`, `/description`, `/reviews`, token expirado/refresh rotativo, `403`, `404` e `429`.
- E2E com Playwright sobre o NOSSO site em Chromium, Firefox e WebKit: importação, link público, imagens permitidas, descrição, avaliações disponíveis, recomendações próprias, modo responsivo, SSR e HTML inspecionável SEM extensão. Nunca usar E2E para raspar o site do Mercado Livre.
- Segurança: SSRF, segredo não exposto, XSS, cache/revalidação e políticas de mídia.

Critérios de aceite:
1. Dados realmente acessíveis aparecem corretamente, com indicação da origem.
2. A ausência de dados não provoca interface quebrada ou avaliação falsa.
3. A página pública pode ser visualizada e inspecionada sem plugin em navegadores comuns.
4. Fotos e informações com uso autorizado são carregadas corretamente.
5. Recomendações exibidas são apenas as configuradas por nós.
6. Nada no site finge ser o marketplace oficial ou reproduz seu checkout.
7. Relatório final inclui: arquivos alterados, comandos para executar, testes rodados, status de cada endpoint, permissões faltantes e qualquer limitação contratual/técnica.

## 9. Como trabalhar

Execute na ordem: auditar -> reproduzir erro -> planejar alterações mínimas -> implementar -> testar -> documentar. Não faça apenas protótipo de interface: entregue rotas backend, autenticação, normalização, banco/adaptadores, telas e testes conforme o projeto permitir. Evite bibliotecas desnecessárias. Mostre no final um exemplo sanitizado de `ProductViewModel` e uma tabela curta de resultados verificados. Não declare sucesso sem testes observáveis.

Fontes prioritárias:
- https://developers.mercadolivre.com.br/pt_br/autenticacao-e-autorizacao/publicacao-de-produtos
- https://developers.mercadolivre.com.br/pt_br/api-docs-pt-br/descricao-de-produtos
- https://developers.mercadolivre.com.br/pt_br/opinioes-sobre-um-produto
- https://developers.mercadolivre.com.br/pt_br/termos-e-condicoes
- https://developers.mercadolivre.com.br/pt_br/publicacao-de-produtos/gestao-de-identidades-e-acessos-oauth-e-tokens

## ADENDO PRIORITÁRIO — Importador universal, inspeção nativa e credenciais por usuário

Este adendo substitui a limitação de escopo a um único marketplace, mas NÃO substitui as regras de autorização e segurança anteriores.

### A. Múltiplas lojas
- Aceitar URL HTTPS de lojas variadas e detectar domínio e plataforma. Criar `SourceAdapter` com `canHandle`, `resolve`, `extract`, `capabilities` e `diagnose` e um adaptador genérico para fontes que permitem leitura automatizada.
- Priorizar, quando o site permitir a captura do HTML, a leitura nativa de DOM/HTML: JSON-LD `Product`/`Offer`/`AggregateRating`/`Review`, OpenGraph, metatags, microdata, HTML semântico e seletores configuráveis. Usar `DOMParser`, `querySelector(All)` e atributos DOM sem dependências de scraping. No backend, reutilizar parser nativo da stack se houver; caso contrário, justificar dependência mínima. NÃO prometer que um navegador consegue ler DOM de origem cross-origin; CORS/same-origin e termos se aplicam.
- Para provedores como Mercado Livre, cuja política não autoriza scraping, usar integrações oficiais permitidas como caminho automático principal. Quando não houver acesso autorizado, mostrar campo ausente, motivo e opção de dados próprios ou importação manual autorizada; nunca contornar anti-bot, CAPTCHA ou autenticação.
- Não copiar checkout, login, branding ou interface que faça visitantes acreditarem estar no domínio da loja original. Não publicar fotos/avaliações sem direitos apropriados.

### B. Central de integrações e chaves próprias
- Criar área autenticada **Configurações > Integrações** por conta/organização: lista de provedores, status, última verificação, criar/editar/testar/revogar conexão e permissões necessárias.
- Formulário dinâmico por provedor: API key, client ID, client secret, OAuth2 com PKCE quando apropriado, escopos, URLs permitidas, cabeçalhos não sensíveis, região e ambiente. Não permitir URLs de API arbitrárias sem validação rígida/allowlist; impedir SSRF.
- Guardar segredos criptografados em repouso no servidor, usando gerenciador de segredos ou criptografia autenticada com chave fora do banco. Nunca enviar segredo de volta à UI; mascarar valores e suportar rotação/revogação.
- Tokens e refresh no backend, com controle de concorrência, isolamento entre tenants, RBAC, trilha de auditoria sem valores secretos, testes de conexão com erro acionável.
- O usuário pode conectar apenas APIs que tenha direito de usar; a chave não garante acesso a todos os produtos de terceiros.

### C. Tela de importação com preview lateral SEMPRE visível no desktop
- Layout responsivo em duas colunas: esquerda URL, fonte, status, diagnóstico por campo, imagens, título, preço, descrição, avaliações, seletores e edição; direita preview fiel à página **própria** publicada, em tempo real.
- No mobile, abas `Dados` e `Prévia` ou preview recolhível.
- Preview isolado por iframe sandbox ou componente seguro; nunca executar scripts externos ou HTML não sanitizado. Atualização incremental, estados carregando/erro/campo não disponível, modo desktop/mobile e botão publicar.
- Recomendados sempre do catálogo interno, não da fonte. Mostrar procedência e data de atualização dos campos importados.

### D. Diagnóstico verificável
- Por campo, guardar `{value, source, selector_or_path, retrieved_at, confidence, status, error}`; preservar URL original, direitos/autorizações e histórico de atualizações.
- Criar testes com fixtures HTML de páginas de comércio autorizadas: JSON-LD, OpenGraph, lazy-loading `srcset`, variações, preço, moeda, descrição, notas e avaliações; validar que não há avaliações inventadas.
- Testar 401/403/429, OAuth expirado, CORS, fontes não permitidas, imagens quebradas, HTML malformado, isolamento entre usuários, vazamento de segredos, SSRF e atualização de preview.
- Entregar implementação funcional na stack existente, migrações, `.env.example`, documentação de provedores e evidências dos testes. Não encerrar com apenas mockups ou planejamento.
