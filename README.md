# Nexo Commerce

Aplicação web para criar páginas próprias de produto a partir de links do Mercado Livre, Shopee, Temu, AliExpress e Amazon. O fluxo gera um rascunho editável, permite conferir a página completa antes da publicação e acompanha visitantes em um mapa do Brasil. O projeto inclui o app Flutter, uma API Python e armazenamento SQLite no próprio servidor.

## Subir no servidor

Requisitos: servidor Linux com Docker Compose, um domínio apontado para o IP do servidor e portas 80/443 liberadas.

```bash
cp .env.example .env
# Edite SITE_DOMAIN em .env com o domínio real.
docker compose up -d --build
```

O Caddy configura HTTPS automaticamente para o domínio informado. Acesse `https://seu-dominio/` para abrir o painel. O usuário padrão é `admin`. Se `NEXO_ADMIN_PASSWORD` ficar vazio, uma senha forte é criada na primeira inicialização e aparece em `docker compose logs app`; ela também fica guardada no volume em `/data/admin_password`.

Para testar somente na máquina local, use `SITE_DOMAIN=:80` no `.env` e acesse `http://localhost/`.

### Prévia no Render

O arquivo `render.yaml` prepara uma prévia pública usando o `Dockerfile`, com HTTPS e domínio `onrender.com` gerenciados pelo Render. Cadastre as variáveis secretas solicitadas durante a criação do serviço e use a URL pública resultante em `ML_REDIRECT_URI`, acrescentando `/api/mercadolivre/callback`. A URL de notificações usa o mesmo domínio com `/api/mercadolivre/notify`.

O plano gratuito serve para validação, mas seu sistema de arquivos é temporário e o serviço pode hibernar quando fica sem tráfego. Para produção, use um plano compatível com disco persistente montado em `/data`, onde ficam o SQLite, tokens, fotos e a base de geolocalização.

## Criar sua página a partir de um link

No painel, abra **Importar produto**, cole o link e clique em **Importar informações**. Revise título, preço, nome da loja, marca, descrição, estoque, frete, variações, fotos e comentários encontrados. Você pode alterar os campos da sua página, trocar URLs de fotos, enviar imagens JPG/PNG/WebP do seu computador para o próprio servidor e ocultar comentários antes de abrir **Ver página completa** e publicar. Depois da publicação, use **Editar sua página** no catálogo para atualizar o mesmo endereço.

O importador é universal para URLs HTTPS, respeitando as permissões da fonte. Em **Configurações → Central de Integrações**, cada conta configura sua conexão OAuth Mercado Livre ou autoriza hosts exatos para leitura de HTML de sua loja. A captura genérica usa o parser nativo Python para JSON-LD, OpenGraph, microdata, imagens lazy/srcset e seletores simples. Sites que dependem de scripts, login ou bloqueios exigem API oficial, HTML autorizado ou dados próprios; não há captura automatizada por navegador. Reviews, notas, preços e imagens ausentes não são inventados.

O editor mantém a prévia lateral visível no desktop e abas **Dados / Prévia** no mobile. A prévia acompanha as alterações de título, preço, descrição, fotos, avaliações selecionadas e recomendações próprias, em iframe isolado. A página publicada usa o mesmo renderizador HTML no servidor e funciona sem extensão e sem JavaScript.

Veja [implementação, contas, provedores e execução](docs/IMPORTADOR_UNIVERSAL.md) e [resultados dos testes e pendências reais](docs/VALIDACAO_UNIVERSAL.md).
O resultado é uma página hospedada no **seu domínio**, com o nome da sua loja e uma URL pública `/p/...`. O botão de compra pode direcionar à oferta original ou usar checkout próprio via PixzyPay. No checkout próprio, o servidor recalcula o preço do produto publicado, cria a cobrança Pix e confirma o pedido por webhook ou reconciliação; o navegador nunca acessa a API da PixzyPay diretamente.

### Pagamentos PixzyPay

Crie um Bearer Token em **Configurações → Tokens de API** na PixzyPay. No painel administrativo do Nexo, abra **Configurações → Pagamento e Pix**, informe o token, a URL da API e a URL pública do webhook e clique em **Salvar configuração**. O token é enviado ao backend por HTTPS, fica no volume privado do servidor e nunca é devolvido ao navegador.

Como alternativa, a configuração pode ser gerenciada pelo ambiente do servidor. Nesse caso, os campos do painel ficam bloqueados e as variáveis têm prioridade:

```env
PIXZY_BASE_URL=https://app.pixzypay.com/api
PIXZY_API_TOKEN=seu_token_secreto
PIXZY_WEBHOOK_URL=https://loja.exemplo.com.br/api/webhooks/pixzypay/transactions
```

Depois de salvar, use **Testar conexão** e, ao publicar um produto, selecione **Checkout próprio**. Nunca coloque `PIXZY_API_TOKEN` em `--dart-define`, no código Flutter, no JavaScript ou em arquivos versionados.

O webhook público é `POST /api/webhooks/pixzypay/transactions`. Como a documentação atual da PixzyPay não define assinatura criptográfica, o backend confere transação, pedido e valor e consulta `GET /transactions/{id}` antes de alterar o status financeiro. Eventos repetidos são deduplicados no SQLite.

### Mercado Livre

Crie um aplicativo no [portal oficial de desenvolvedores](https://developers.mercadolivre.com.br/pt_br/configuracao-ou-requisitos-previos) e cadastre a URL de retorno `https://seu-dominio/api/mercadolivre/callback`. Preencha `ML_CLIENT_ID`, `ML_CLIENT_SECRET` e `ML_REDIRECT_URI` no `.env`, depois execute `docker compose up -d`.

Na Central de Integrações, configure o aplicativo da sua conta e use **Autorizar conta**. Para novas conexões, cadastre `https://seu-dominio/api/integrations/mercadolivre/callback`; a configuração legada via ambiente permanece restrita ao administrador original. Links `/p/MLB...` precisam identificar uma publicação selecionada e pertencente ao catálogo; o sistema não escolhe vendedor ou anúncio arbitrariamente. A API fornece apenas os campos permitidos. Não são buscados HTML/DOM do Mercado Livre, e imagens não são copiadas sem autorização. Sem acesso, use dados próprios e registre os direitos de publicação.
Ao publicar, o produto fica salvo no SQLite. Dados importados como preço, estoque e avaliações refletem o momento da importação; você pode editar preço e estoque na sua página ou importar novamente. A integração de pagamentos Pix fica disponível quando as três variáveis `PIXZY_*` estão configuradas. No modo de servidor, as telas de pedidos e configurações não mostram dados fictícios; os gráficos de faturamento pertencem somente ao modo de demonstração local.

## Tráfego em tempo real

A página pública envia um evento de visita e atualizações a cada 30 segundos para a API no mesmo domínio. O servidor conta sessões ativas nos últimos cinco minutos e guarda somente um identificador aleatório, a página, o horário e a localização aproximada; endereços IP não são gravados no SQLite. O painel consulta a API a cada 15 segundos. O mapa mostra visitantes por cidade brasileira e identifica os que não puderam ser localizados.

A malha dos estados é do [IBGE](https://servicodados.ibge.gov.br/api/v3/malhas/paises/BR?intrarregiao=UF&qualidade=minima). A localização aproximada por IP usa a base gratuita [DB-IP City Lite](https://db-ip.com/db/download/ip-to-city-lite), baixada para o próprio servidor na primeira inicialização e atualizada mensalmente. Essa base tem cobertura e precisão limitadas. A interface dá a atribuição exigida pela licença. Até a base terminar de baixar, o mapa indica que a localização está em preparação.

O banco de visitas, os produtos publicados, os tokens do Mercado Livre e as fotos copiadas ficam no volume `nexo_data`. Faça backup desse volume antes de migrar o servidor. A API de tráfego e o painel exigem autenticação; as páginas de produto são públicas.

## Desenvolvimento local

No VS Code, selecione **Nexo Commerce (app + API local)** em **Executar e Depurar** e clique em Play. Esse comando compila o Flutter com a API habilitada, inicia o servidor Python em `http://127.0.0.1:8080` (ou na próxima porta livre) e abre o navegador padrão. O login local é `admin` / `local-dev`. O banco local fica em `.local/data`.

Pelo terminal, execute o mesmo fluxo com:

```bash
flutter pub get
python3 scripts/run_local.py
```

Para o Mercado Livre, copie `.env.example` para `.env`, configure `ML_CLIENT_ID`, `ML_CLIENT_SECRET` e `ML_REDIRECT_URI` e reinicie o processo local. O inicializador carrega esse arquivo sem sobrescrever variáveis já definidas no terminal. Depois, clique em **Conectar conta do Mercado Livre** no painel; a API oficial exige um token. A URI de retorno precisa usar HTTPS e ser exatamente a mesma cadastrada no aplicativo do Mercado Livre, portanto o fluxo OAuth normalmente deve ser concluído pelo domínio publicado, não por `127.0.0.1`. Navegadores são necessários apenas para testes E2E do nosso site e são instalados separadamente. O conteúdo importado é um rascunho para sua própria página; copiar o HTML bruto de uma loja não preserva seus scripts, autenticação ou recursos externos.

## Validar

```bash
flutter analyze
flutter test
python3 -m unittest discover -s server -p 'test_*.py'
flutter build web --release --dart-define=NEXO_SELF_HOSTED=true
```
