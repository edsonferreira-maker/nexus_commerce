"""Accessible public HTML and an operator form; no marketplace HTML is fetched."""
import html
import time
import urllib.parse
from mercadolivre import approved_media


def escape(value):
    return html.escape(str(value or ''), quote=True)


def safe_destination(value):
    try:
        parsed = urllib.parse.urlsplit(value or '')
        return value if parsed.scheme == 'https' and parsed.hostname and not parsed.username and not parsed.password and parsed.port in (None, 443) else '#'
    except ValueError:
        return '#'


def local_media(value):
    import re
    return isinstance(value, str) and re.fullmatch(r'/media/upload-[a-f0-9]{32}\.(jpg|png|webp)', value) is not None


def allowed_product_media(value, product):
    if local_media(value):
        return True
    if product.get('market') == 'Mercado Livre':
        return approved_media(value)
    from universal_import import validate_url
    try:
        validate_url(value, product.get('allowedMediaHosts', []))
        return True
    except ValueError:
        return False


def render_product(product, recommendations, ttl=900):
    e = escape
    images = [u for u in product.get('images', []) if allowed_product_media(u, product)]
    gallery = ''.join(f'<figure><img src="{e(u)}" alt="{e(product.get("name"))} — foto {i+1}" width="600" height="600" loading="lazy"><figcaption>Imagem do anúncio original. Se não carregar, consulte a fonte.</figcaption></figure>' for i, u in enumerate(images)) or '<p>Fotos indisponíveis.</p>'
    own = product.get('catalogOwner') == 'own_catalog'
    stale = not own and time.time() - product.get('syncedAt', 0) > ttl
    price = 'Preço desatualizado. Consulte o anúncio original.' if stale and product.get('provenance', {}).get('price') != 'user_supplied' else product.get('price') or 'Preço indisponível.'
    reviews = ''.join(f'<article><p>{e(r.get("comment"))}</p><small>{e(r.get("author"))} · {e(r.get("date"))} · {e(str(r["stars"]) + "/5" if r.get("stars") else "Nota indisponível")}</small></article>' for r in product.get('reviews', [])) or '<p>Avaliações indisponíveis.</p>'
    attrs = ''.join(f'<dt>{e(a.get("name"))}</dt><dd>{e(a.get("value_name"))}</dd>' for a in product.get('attributes', []) if isinstance(a, dict))
    recs = ''.join(f'<article><h3><a href="/produto/{e(r["slug"])}">{e(r.get("name"))}</a></h3><p>Vendedor: {e(r.get("storeName"))}</p></article>' for r in recommendations) or '<p>Nenhuma recomendação cadastrada.</p>'
    status = ''.join(f'<li>{e(k)}: {e("stale" if k == "price" and stale and product.get("provenance", {}).get("price") != "user_supplied" else v.get("state"))}</li>' for k,v in product.get('sourceStatus', {}).items())
    source_label = 'Informações da nossa loja' if own else 'Informações do anúncio original'
    source_name = 'Catálogo próprio' if own else product.get('market', 'Fonte externa')
    source_action = 'Visitar loja do vendedor' if own else f'Consultar anúncio original em {source_name}'
    review_section = '' if own else '<h2>Avaliações de ' + e(source_name) + '</h2><p>' + e(product.get('rating') or 'Nota indisponível') + '</p>' + reviews
    return shell(product.get('name', 'Produto'), f'''<header><a href="/">Nexo Commerce</a><p aria-label="Loja">{e(product.get("storeName") or "Minha loja")}</p></header><main>
<h1>{e(product.get('name'))}</h1><section aria-label="Galeria">{gallery}</section>
<h2>{source_label}</h2><p>{e(price)}</p>
<p>Fonte: {e(source_name)}. Última sincronização: {e(product.get('fetchedAt'))}</p>
<a href="{e(safe_destination(product.get('sourceUrl')))}" rel="noopener noreferrer">{source_action}</a>
<h2>Descrição</h2><p class="description">{e(product.get('description') or 'Descrição indisponível.')}</p>
<dl>{attrs}</dl>{review_section}
<details><summary>Disponibilidade dos dados</summary><ul>{status}</ul></details>
<section><h2>Recomendações da nossa loja</h2>{recs}</section></main>''', product.get('description', '')[:160], images[0] if images else '')


def shell(title, body, description='', image=''):
    return f'''<!doctype html><html lang="pt-BR"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{escape(title)} | Nexo Commerce</title><meta name="description" content="{escape(description)}">
<meta property="og:title" content="{escape(title)}"><meta property="og:description" content="{escape(description)}"><meta property="og:type" content="product"><meta property="og:image" content="{escape(image)}">
<style>body{{overflow-wrap:anywhere;font:18px system-ui;color:#172436;background:#f5f7fb;margin:0}}main,header{{max-width:1040px;margin:auto;padding:24px}}section{{display:flex;flex-wrap:wrap;gap:20px}}section h2{{width:100%}}figure{{margin:0;flex:1 1 240px}}img{{width:100%;height:auto;object-fit:contain}}article{{padding:16px;background:white;border-radius:12px}}a{{color:#185ca8}}a:focus,input:focus,button:focus{{outline:3px solid #d27900}}.description{{white-space:pre-wrap}}pre{{white-space:pre-wrap}}input,button{{font:inherit;padding:10px;max-width:100%;box-sizing:border-box}}label{{display:block;margin:12px 0}}@media(max-width:600px){{main,header{{padding:16px}}}}</style></head><body>{body}</body></html>'''


def render_admin(products):
    options = ''.join(f'<label><input type="checkbox" name="recommendation" value="{escape(p["slug"])}">{escape(p.get("name"))} — {escape(p.get("storeName"))}</label>' for p in products if p.get('catalogOwner') == 'own_catalog')
    return shell('Importador universal', r'''<style>
main.editor{max-width:1600px}.toolbar{display:flex;gap:16px;align-items:center;flex-wrap:wrap}.workspace{display:grid;grid-template-columns:minmax(300px,1fr) minmax(360px,1fr);gap:28px;align-items:start}.panel{padding:24px;background:white;border:1px solid #dce4ee;border-radius:16px}.preview-panel{position:sticky;top:16px}.preview-panel iframe{width:100%;height:75vh;min-height:460px;border:1px solid #dce4ee;background:#f5f7fb;border-radius:12px}.preview-panel.mobile-preview iframe{max-width:390px;display:block;margin:auto}input:not([type=checkbox]),textarea,select{display:block;width:100%;font:inherit;padding:10px;border:1px solid #9baac0;border-radius:6px;box-sizing:border-box}textarea{min-height:110px}.tabs{display:none}button{cursor:pointer;border-radius:8px;border:1px solid #aebcd0;background:#edf3fc}.primary{background:#185ca8;color:white}#result{font-size:14px;max-height:180px;overflow:auto}.diagnostic{font-size:13px;border-bottom:1px solid #dce4ee;padding:10px 0}#status{min-height:24px}label small{display:block;color:#52647b}
@media(max-width:780px){.workspace{display:block}.tabs{display:flex;gap:12px;margin:16px 0}.workspace.show-data .preview-panel,.workspace.show-preview .data-panel{display:none}.preview-panel{position:static}.panel{padding:16px}}
</style><main class="editor"><div class="toolbar"><h1>Importador universal</h1><a href="/admin/integrations">Configurações → Integrações</a><a href="/">Painel</a></div>
<p>Crie sua página com dados de APIs ou de lojas que autorizam leitura automatizada. Confira a origem e os direitos antes de publicar.</p>
<div class="tabs"><button id="tab-data">Dados</button><button id="tab-preview">Prévia</button></div>
<div class="workspace show-data" id="workspace"><div class="panel data-panel">
<details><summary>Cadastrar produto próprio</summary><form id="catalog"><label>Produto<input id="own-name" required></label><label>Vendedor<input id="own-seller" required></label><label>Destino HTTPS<input id="own-url" type="url" required></label><label><input id="own-rights" type="checkbox" required>Confirmo que este produto pertence ao nosso catálogo e tenho autorização para divulgá-lo.</label><button>Cadastrar no catálogo</button></form></details>
<form id="import"><label>URL HTTPS ou MLB<input id="url" required placeholder="https://sua-loja.com/produto"></label>
<label>Origem<select id="mode"><option value="automatic">API / loja autorizada</option><option value="html">HTML autorizado</option><option value="manual">Dados próprios / preenchimento manual</option></select></label>
<label id="html-label" hidden>HTML fornecido por você<textarea id="html" maxlength="8000000" placeholder="Cole apenas HTML que você tem autorização para importar"></textarea><small>O HTML será analisado como texto. Scripts não serão executados.</small></label>
<label id="manual-rights-label" hidden><input id="manual-rights" type="checkbox">Tenho autorização para usar este conteúdo.</label><button class="primary">Importar</button></form>
<p id="status" role="status" aria-live="polite">Cole uma URL para começar.</p><details><summary>Diagnóstico por campo</summary><div id="diagnostics"></div><pre id="result"></pre></details>
<form id="publish" hidden><label>Nome da loja<input id="edit-storeName" maxlength="80" required></label><label>Título<input id="edit-name" maxlength="200" required></label><label>Preço (vazio quando indisponível)<input id="edit-price" maxlength="80"></label><label>Descrição<textarea id="edit-description" maxlength="10000"></textarea></label><label>Imagens HTTPS (uma por linha, até 10)<textarea id="edit-images"></textarea><small>Apenas origens de mídia autorizadas na conexão ou arquivos enviados ao Nexo.</small></label>
<label>Enviar foto própria<input id="upload" type="file" accept="image/png,image/jpeg,image/webp"></label><fieldset><legend>Avaliações importadas</legend><div id="review-list"></div></fieldset>
<h2>Recomendações da nossa loja</h2><p>Selecione somente produtos próprios e defina a ordem abaixo.</p>''' + options + r'''<label>Slugs ordenados (opcional, separados por vírgula)<input id="ordered"></label>
<label><input id="rights" type="checkbox" required>Confirmo autorização para publicar os textos, imagens e avaliações selecionados.</label><button class="primary">Publicar</button></form><a id="public" hidden>Ver página pública</a></div>
<aside class="panel preview-panel" id="preview"><div class="toolbar"><h2>Prévia em tempo real</h2><button id="desktop" type="button">Desktop</button><button id="mobile" type="button">Mobile</button></div><p id="preview-state" role="status">A prévia aparecerá aqui.</p><iframe id="preview-frame" title="Prévia da sua página de produto" sandbox="" referrerpolicy="no-referrer"></iframe></aside></div></main>
<script>
let draft, timer, revision=0;
const $=s=>document.querySelector(s), result=$('#result'), status=$('#status');
async function post(url,body,html=false){const r=await fetch(url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});if(html&&r.ok)return r.text();const data=await r.json();if(!r.ok)throw Error(data.message||'Falha na solicitação');return data;}
function edits(){const page={};for(const k of ['name','price','storeName','description'])page[k]=$('#edit-'+k).value;page.images=$('#edit-images').value.split(/\r?\n/).map(v=>v.trim()).filter(Boolean);page.reviews=Array.from(document.querySelectorAll('[name=review]:checked')).map(i=>draft.reviews[Number(i.value)]);return page;}
function recommendations(){const order=$('#ordered').value;return order?order.split(',').map(s=>s.trim()).filter(Boolean):Array.from(document.querySelectorAll('[name=recommendation]:checked')).map(i=>i.value);}
function schedulePreview(){const version=++revision;clearTimeout(timer);$('#preview-state').textContent='Atualizando prévia…';timer=setTimeout(async()=>{if(!draft)return;try{const html=await post('/api/products/preview',{id:draft.id,page:edits(),recommendations:recommendations()},true);if(version!==revision)return;$('#preview-frame').srcdoc=html;$('#preview-state').textContent='Prévia atualizada';}catch(e){if(version===revision)$('#preview-state').textContent=e.message;}},180);}
function fill(product){draft=product;for(const k of ['name','price','storeName','description'])$('#edit-'+k).value=product[k]||'';$('#edit-images').value=(product.images||[]).join('\n');$('#review-list').replaceChildren();for(const [i,review] of (product.reviews||[]).entries()){const label=document.createElement('label'), input=document.createElement('input');input.type='checkbox';input.name='review';input.value=i;input.checked=true;label.append(input,document.createTextNode(' '+review.comment));$('#review-list').append(label);}if(!product.reviews?.length)$('#review-list').textContent='Avaliações indisponíveis. Nenhuma será criada.';$('#diagnostics').replaceChildren();for(const [field,d] of Object.entries(product.fieldDiagnostics||{})){const node=document.createElement('div');node.className='diagnostic';node.textContent=field+': '+d.status+' · '+d.source+' · '+(d.selector_or_path||'sem caminho')+' · '+d.retrieved_at+(d.error?' · '+d.error:'');$('#diagnostics').append(node);}result.textContent=JSON.stringify(product,null,2);$('#publish').hidden=false;$('#public').hidden=true;schedulePreview();}
$('#mode').onchange=()=>{const mode=$('#mode').value;$('#html-label').hidden=mode!=='html';$('#manual-rights-label').hidden=mode==='automatic';};
$('#publish').addEventListener('input',schedulePreview);
$('#upload').onchange=async()=>{const file=$('#upload').files[0];if(!file)return;try{const r=await fetch('/api/media/upload',{method:'POST',headers:{'Content-Type':'application/octet-stream'},body:file});const data=await r.json();if(!r.ok)throw Error(data.message);$('#edit-images').value=[$('#edit-images').value,data.url].filter(Boolean).join('\n');schedulePreview();}catch(e){status.textContent=e.message;}};
$('#desktop').onclick=()=>$('#preview').classList.remove('mobile-preview');$('#mobile').onclick=()=>$('#preview').classList.add('mobile-preview');
$('#tab-data').onclick=()=>$('#workspace').className='workspace show-data';$('#tab-preview').onclick=()=>$('#workspace').className='workspace show-preview';
$('#catalog').onsubmit=async e=>{e.preventDefault();try{await post('/api/catalog/products',{name:$('#own-name').value,storeName:$('#own-seller').value,sourceUrl:$('#own-url').value,rightsConfirmed:$('#own-rights').checked});location.reload();}catch(e){status.textContent=e.message;}};
$('#import').onsubmit=async e=>{e.preventDefault();status.textContent='Importando…';$('#preview-state').textContent='Carregando dados…';const button=e.target.querySelector('button');button.disabled=true;try{const mode=$('#mode').value,body={url:$('#url').value,revalidate:true,authorizationConfirmed:$('#manual-rights').checked};if(mode==='html')body.html=$('#html').value;if(mode==='manual')body.manual=true;fill(await post('/api/products/import',body));status.textContent='Rascunho importado. Revise os dados e direitos.';}catch(e){status.textContent=e.message;$('#preview-state').textContent='Falha na importação. '+e.message;}finally{button.disabled=false;}};
$('#publish').onsubmit=async e=>{e.preventDefault();const button=e.target.querySelector('button');button.disabled=true;try{const p=await post('/api/products/publish',{id:draft.id,page:edits(),redirect:true,rightsConfirmed:$('#rights').checked,recommendations:recommendations()});$('#public').href='/produto/'+p.slug;$('#public').hidden=false;status.textContent='Publicado';result.textContent='Publicado';}catch(e){status.textContent=e.message;}finally{button.disabled=false;}};
const initial=new URLSearchParams(location.search).get('id');if(initial)fetch('/api/products/draft?id='+encodeURIComponent(initial)).then(async r=>{const p=await r.json();if(!r.ok)throw Error(p.message);$('#url').value=p.inputUrl||p.sourceUrl;fill(p);status.textContent='Página carregada para edição.';}).catch(e=>status.textContent=e.message);
</script>''')


def render_integrations(identity):
    accounts = r'''<details><summary>Criar conta / usuário de organização</summary><form id="account"><label>Usuário<input id="username" required></label><label>Senha inicial<input id="password" type="password" minlength="12" autocomplete="new-password" required></label><label>Organização existente (vazio cria uma nova)<input id="tenant"></label><label>Perfil<select id="role"><option value="owner">Proprietário</option><option value="editor">Editor</option><option value="viewer">Leitor</option></select></label><button>Criar usuário</button></form></details>''' if identity['tenant'] == 'admin' and identity['role'] == 'owner' else ''
    return shell('Central de Integrações', r'''<style>form{padding:24px;background:white;border-radius:14px;margin-bottom:24px}input:not([type=checkbox]),textarea,select{display:block;width:100%;font:inherit;padding:10px;box-sizing:border-box}textarea{min-height:90px}button{margin:4px}small{display:block;color:#52647b}.connection-status{padding:12px;background:#edf3fc;border-radius:8px}.actions{display:flex;gap:12px;flex-wrap:wrap}</style><main><a href="/">Painel</a> · <a href="/admin/import">Importar produto</a><h1>Configurações → Integrações</h1><p>Conta: ''' + escape(identity['user']) + r''' · Perfil: ''' + escape(identity['role']) + r'''</p><p>Use credenciais de APIs que você tem direito de acessar. As chaves ficam criptografadas no servidor e nunca são devolvidas nesta tela.</p><p id="result" role="status" aria-live="polite"></p>
<form id="mercadolivre"><h2>Mercado Livre · OAuth2</h2><p class="connection-status" id="ml-status">Carregando…</p><p>Autorize sua conta; habilite offline_access no aplicativo para renovação. Uma chave não garante acesso aos anúncios de terceiros.</p><label>Client ID<input id="clientId" required maxlength="2048"></label><label>Client Secret<input id="clientSecret" type="password" autocomplete="new-password" maxlength="2048"><small>Deixe vazio para manter o segredo salvo.</small></label><label>URL de retorno HTTPS<input id="redirectUri" type="url" required></label><small>Cadastre no provedor exatamente a URL exibida, terminando em /api/integrations/mercadolivre/callback.</small><label>Escopos (conforme configuração do aplicativo)<input id="scopes"></label><label><input id="pkce" type="checkbox">PKCE habilitado no aplicativo (S256)</label><div class="actions"><button>Salvar / rotacionar</button><button type="button" id="connect">Autorizar conta</button><button type="button" data-test="mercadolivre">Testar conexão</button><button type="button" data-revoke="mercadolivre">Revogar no Nexo</button></div></form>
<form id="generic"><h2>Loja autorizada · HTML / JSON-LD</h2><p class="connection-status" id="generic-status">Carregando…</p><p>Leitura nativa de HTML com JSON-LD, OpenGraph, microdata e seletores. Scripts da loja não serão executados. Bloqueios e login exigem uma integração oficial ou dados próprios.</p><label>Domínios exatos autorizados (um por linha)<textarea id="allowedHosts" required placeholder="loja.exemplo.com.br"></textarea></label><label>Domínios de mídia autorizados (um por linha)<textarea id="mediaHosts" placeholder="cdn.exemplo.com.br"></textarea></label><label>API key / Bearer (opcional)<input id="apiKey" type="password" autocomplete="new-password"><small>Enviada somente ao domínio original; não acompanha redirecionamento para outro domínio. Vazio mantém o valor salvo.</small></label><label>Cabeçalhos não sensíveis (JSON: Accept-Language, X-Region)<textarea id="headers">{}</textarea></label><label>Seletores (JSON: name, price, currency, description, images)<textarea id="selectors">{}</textarea></label><small>Seletores simples: h1, #titulo, .preco, [itemprop="price"]. Prefira JSON-LD quando disponível.</small><label>Região<input id="region" maxlength="80"></label><label>Ambiente<input id="environment" maxlength="80" placeholder="production"></label><label><input id="authorizationConfirmed" type="checkbox" required>Confirmo autorização de leitura automatizada e os direitos aplicáveis ao conteúdo.</label><label>URL de produto para testar<input id="test-url" type="url"></label><div class="actions"><button>Salvar / rotacionar</button><button type="button" data-test="generic">Testar conexão</button><button type="button" data-revoke="generic">Revogar no Nexo</button></div></form>
<p>Ao revogar no Nexo, as credenciais locais são removidas. Revogue também o acesso no painel do provedor.</p>''' + accounts + r'''</main><script>
const $=s=>document.querySelector(s), result=$('#result');
async function post(url,body){const response=await fetch(url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});const data=await response.json();if(!response.ok)throw Error(data.message||'Falha na solicitação');return data;}
const lines=id=>$('#'+id).value.split(/\r?\n/).map(v=>v.trim()).filter(Boolean);
async function load(){const response=await fetch('/api/integrations');if(!response.ok)throw Error('Faça login novamente.');const data=await response.json();for(const p of data.providers){const c=p.config;$('#'+(p.id==='mercadolivre'?'ml':'generic')+'-status').textContent=p.status+' · '+(p.checkedAt?new Date(p.checkedAt*1000).toLocaleString():'Ainda não verificada')+' · '+(c.credential?'Credencial armazenada':'Sem segredo')+' '+p.message;for(const field of p.fields){const input=$('#'+field);if(!input||field==='clientSecret'||field==='apiKey')continue;if(input.type==='checkbox')input.checked=c[field]===true;else if(Array.isArray(c[field]))input.value=c[field].join('\n');else if(c[field]&&typeof c[field]==='object')input.value=JSON.stringify(c[field],null,2);else if(c[field]!==undefined)input.value=c[field];}if(p.id==='mercadolivre'&&!c.redirectUri)$('#redirectUri').value=location.origin+'/api/integrations/mercadolivre/callback';}if(data.identity.role!=='owner'){document.querySelectorAll('form input,form button,form textarea').forEach(e=>e.disabled=true);}}
$('#mercadolivre').onsubmit=async e=>{e.preventDefault();try{const body={pkce:$('#pkce').checked};for(const k of ['clientId','clientSecret','redirectUri','scopes'])body[k]=$('#'+k).value;await post('/api/integrations/mercadolivre/save',body);$('#clientSecret').value='';result.textContent='Configuração salva.';await load();}catch(e){result.textContent=e.message;}};
$('#generic').onsubmit=async e=>{e.preventDefault();try{await post('/api/integrations/generic/save',{allowedHosts:lines('allowedHosts'),mediaHosts:lines('mediaHosts'),apiKey:$('#apiKey').value,headers:JSON.parse($('#headers').value),selectors:JSON.parse($('#selectors').value),region:$('#region').value,environment:$('#environment').value,authorizationConfirmed:$('#authorizationConfirmed').checked});$('#apiKey').value='';result.textContent='Configuração salva.';await load();}catch(e){result.textContent=e.message;}};
$('#connect').onclick=()=>location.href='/api/integrations/mercadolivre/connect';
for(const button of document.querySelectorAll('[data-test],[data-revoke]'))button.onclick=async()=>{button.disabled=true;try{const provider=button.dataset.test||button.dataset.revoke,action=button.dataset.test?'test':'revoke';result.textContent=action==='test'?'Verificando conexão real…':'Revogando…';const data=await post('/api/integrations/'+provider+'/'+action,{url:$('#test-url').value});result.textContent=data.message||'Credenciais removidas.';await load();}catch(e){result.textContent=e.message;}finally{button.disabled=false;}};
if($('#account'))$('#account').onsubmit=async e=>{e.preventDefault();try{const data=await post('/api/accounts',{username:$('#username').value,password:$('#password').value,tenant:$('#tenant').value,role:$('#role').value});$('#password').value='';result.textContent='Usuário criado. Organização: '+data.tenant;}catch(e){result.textContent=e.message;}};
load().catch(e=>result.textContent=e.message);
</script>''')
