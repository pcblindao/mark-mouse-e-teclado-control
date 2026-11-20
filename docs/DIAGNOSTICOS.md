# Diagnósticos e minimização

Os diagnósticos locais são inicializados no ponto de entrada da aplicação. Os eventos cobrem inicialização, erros capturados no agente, callbacks Tk, operações remotas e transferência, disponibilidade da bandeja e ação de minimizar. Não cobrem todas as falhas de rede, travamentos nativos ou encerramentos forçados.

Exceções são registradas pelo tipo e pelos últimos frames (nome do arquivo, função e linha), sem texto da exceção, código-fonte, variáveis locais ou caminho completo. O logger é exclusivo e não captura mensagens de bibliotecas ou comandos de entrada. Quatro arquivos rotativos de 256 KiB limitam o uso do disco. Falha ao abrir o registro não impede a inicialização.

O botão **Configurações → Enviar diagnóstico** mostra uma prévia limitada dos eventos recentes e abre um formulário de issue do repositório. O usuário precisa entrar no GitHub, descrever a falha e confirmar a publicação. Nenhuma issue é criada automaticamente. Versões já baixadas precisam ser atualizadas para ter esse recurso.

Para investigar o notebook, reproduzir a minimização com e sem uma sessão ativa e abrir o diagnóstico após restaurar a janela. Verificar os eventos `window_hidden_in_tray`, `window_minimized_without_tray`, `tray_failed`, `tray_update_failed`, `tray_notification_failed` e `ui_callback_failed`. Registrar também a versão do Windows e se o ícone está acessível na área de notificação.

Ao minimizar com a bandeja disponível, a janela é escondida antes de qualquer notificação. Falhas da notificação são registradas sem abrir um diálogo modal. Sem bandeja, a janela é minimizada para a barra de tarefas. Essa correção elimina um bloqueio possível no código; a reprodução no notebook afetado continua necessária para confirmar a causa do relato.

Um futuro envio automático exige um coletor HTTPS autenticado no servidor, com limite de abuso, política de retenção e preferência explícita no aplicativo. A credencial de criação de issues deve ficar exclusivamente no servidor. Esta alteração não instala esse serviço nem inclui tokens no executável.
