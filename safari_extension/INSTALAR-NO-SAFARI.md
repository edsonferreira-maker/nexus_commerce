# Instalar o conector no Safari

1. Abra `Nexo Commerce Connector/Nexo Commerce Connector.xcodeproj` no Xcode.
2. Na barra superior do Xcode, selecione o esquema **Nexo Commerce Connector** e o destino **My Mac**.
3. Pressione o botão **Run** (triângulo) ou use `Command + R`.
4. Quando o aplicativo abrir, clique em **Quit and Open Safari Settings**.
5. No Safari, abra **Ajustes > Extensões** e ative **Nexo Commerce Connector**.
6. Autorize o acesso a `nexus-commerce-1n5m.onrender.com` e aos sites do Mercado Livre, escolhendo **Sempre permitir** quando o Safari perguntar.
7. Mantenha sua conta aberta no Mercado Livre e recarregue o Nexo Commerce.

Depois da ativação, basta colar o link do produto no Nexo. O conector abre a página em segundo plano, captura as informações e fecha a aba automaticamente. Não é necessário abrir o inspetor nem copiar HTML.

Se o Xcode pedir uma equipe de assinatura, selecione sua conta em **Signing & Capabilities > Team** nos dois alvos do projeto. Para executar localmente no próprio Mac, uma conta Apple gratuita é suficiente.
