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

Para Shopee, Temu, AliExpress e Amazon, o servidor tenta ler metadados estruturados JSON-LD e Open Graph da página pública. Quando o HTML inicial está incompleto, um Chromium carrega a página e captura o DOM depois da execução do JavaScript, como no inspetor do navegador. A disponibilidade dos dados depende da loja e do anúncio. Se houver bloqueio ou login, o sistema mostra a falha e você pode colar o HTML no campo opcional **HTML da página**. Comentários só aparecem quando estão disponíveis nos dados da página; nenhum comentário é criado automaticamente. Fotos que não estiverem no seu servidor dependem da disponibilidade da URL de origem; o envio de fotos locais evita essa dependência. Use fotos e textos que você tenha direito de publicar na sua página.

O resultado é uma página hospedada no **seu domínio**, com o nome da sua loja e uma URL pública `/p/...`. O botão de compra direciona à oferta original. O checkout próprio ainda não está implementado.

### Mercado Livre

Crie um aplicativo no [portal oficial de desenvolvedores](https://developers.mercadolivre.com.br/pt_br/configuracao-ou-requisitos-previos) e cadastre a URL de retorno `https://seu-dominio/api/mercadolivre/callback`. Preencha `ML_CLIENT_ID`, `ML_CLIENT_SECRET` e `ML_REDIRECT_URI` no `.env`, depois execute `docker compose up -d`.

Clique em **Conectar conta do Mercado Livre** e autorize o aplicativo para usar a API oficial. Para um link de catálogo `/p/MLB...`, o importador usa o anúncio selecionado no parâmetro `wid` quando ele pertence ao catálogo; caso contrário, usa a oferta disponível. A API fornece título, preço, estoque, fotos, descrição, variações e avaliações, e as fotos são copiadas para o volume quando possível. Sem a conexão, o sistema tenta a página pública e o DOM renderizado; se o Mercado Livre exigir verificação, a importação só continua com uma autorização válida ou com o HTML capturado no seu navegador.

Para capturar a página usando uma sessão já autenticada no Safari, baixe `nexo-mercadolivre-safari.zip`, abra o projeto do Xcode incluído, execute o esquema **Nexo Commerce Connector** em **My Mac** e habilite a extensão em **Safari > Ajustes > Extensões**. O guia completo está em `safari_extension/INSTALAR-NO-SAFARI.md`.

Ao publicar, o produto fica salvo no SQLite. Dados importados como preço, estoque e avaliações refletem o momento da importação; você pode editar preço e estoque na sua página ou importar novamente. A integração de pedidos e pagamentos ainda não está disponível. No modo de servidor, as telas de pedidos e configurações não mostram dados fictícios; os gráficos de faturamento pertencem somente ao modo de demonstração local.

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

Para o Mercado Livre, copie `.env.example` para `.env`, configure `ML_CLIENT_ID`, `ML_CLIENT_SECRET` e `ML_REDIRECT_URI` e reinicie o processo local. O inicializador carrega esse arquivo sem sobrescrever variáveis já definidas no terminal. Depois, clique em **Conectar conta do Mercado Livre** no painel; a API oficial exige um token. A URI de retorno precisa usar HTTPS e ser exatamente a mesma cadastrada no aplicativo do Mercado Livre, portanto o fluxo OAuth normalmente deve ser concluído pelo domínio publicado, não por `127.0.0.1`. No primeiro início local, o script instala o Chromium de captura em `.local/browsers`. O conteúdo importado é um rascunho para sua própria página; copiar o HTML bruto de uma loja não preserva seus scripts, autenticação ou recursos externos.

## Validar

```bash
flutter analyze
flutter test
python3 -m unittest discover -s server -p 'test_*.py'
flutter build web --release --dart-define=NEXO_SELF_HOSTED=true
```
