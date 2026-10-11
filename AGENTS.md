# Instruções do projeto para Codex

## Padrões obrigatórios

- Antes de implementar integrações web/marketplace, leia `.agents/skills/web-api-marketplace-specialist/SKILL.md`.
- Respeite a arquitetura e as dependências existentes; não reescreva o projeto por conveniência.
- Dados de terceiros: usar apenas APIs e permissões autorizadas. Não fazer scraping proibido, copiar a página/identidade visual do Mercado Livre ou ocultar a origem das avaliações e preços.
- A página pública do nosso domínio deve usar HTML renderizado no servidor, ser acessível, responsiva e funcionar sem plugin/extensão.
- APIs externas só no backend; nunca enviar segredos OAuth ao browser. Validação anti-SSRF para URLs externas.
- Respeitar direitos de imagem, autoria, marcas e privacidade. O catálogo de recomendações é exclusivamente nosso e claramente identificado.
- Não inventar dados. Documentar campos faltantes e razões verificadas.
- Implementar e executar testes proporcionais às mudanças; reportar falhas conhecidas e comandos executados.
- O produto é multiloja: seguir o ADENDO PRIORITÁRIO de `PROMPT_CODEX.md`, com captura DOM nativa quando autorizada, preview lateral e central de credenciais por usuário/organização.
- Nunca registrar ou exibir segredos; aplicar criptografia em repouso, isolamento por tenant e validação SSRF.
