# Instalar o conector no Safari

1. Abra `Nexo Commerce Connector/Nexo Commerce Connector.xcodeproj` no Xcode.
2. Na barra superior do Xcode, selecione o esquema **Nexo Commerce Connector** e o destino **My Mac**.
3. Pressione o botão **Run** (triângulo) ou use `Command + R` e mantenha o aplicativo aberto.
4. No Safari, abra **Ajustes > Avançado** e ative **Mostrar recursos para desenvolvedores web**.
5. Na barra de menus do Safari, abra **Desenvolvedor** e marque **Permitir extensões não assinadas**. Confirme com a senha ou Touch ID do Mac.
6. Feche e abra novamente o Safari.
7. Abra **Safari > Ajustes > Extensões** e ative **Nexo Commerce Connector**.
8. Autorize o acesso a `nexus-commerce-1n5m.onrender.com` e aos sites do Mercado Livre, escolhendo **Sempre permitir** quando o Safari perguntar.
9. Mantenha sua conta aberta no Mercado Livre e recarregue o Nexo Commerce.

Depois da ativação, basta colar o link do produto no Nexo. O conector abre a página em segundo plano, captura as informações e fecha a aba automaticamente. Não é necessário abrir o inspetor nem copiar HTML.

Como alternativa a permitir extensões não assinadas, adicione sua conta Apple em **Xcode > Settings > Accounts** e selecione sua equipe em **Signing & Capabilities > Team** nos dois alvos do projeto.
