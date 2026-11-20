# Diagnósticos e minimização — v0.5.1

Os registros locais incluem inicialização, bandeja, minimização e falhas capturadas do agente, callbacks Tk, operações remotas e transferência. Não cobrem todos os erros de rede, travamentos nativos ou encerramentos forçados. Exceções incluem somente tipo e frames (arquivo, função e linha), sem texto, variáveis ou caminhos completos. O logger exclusivo não captura comandos de entrada.

## Autorização e envio

Na primeira abertura visível, perguntar se o usuário autoriza diagnósticos automáticos. Recusar não limita recursos. Cancelar não concede autorização. O modo agente não abre a pergunta e somente envia se houver autorização já salva. A preferência fica em diagnostic-consent.json, com versão do consentimento, e nas Configurações. Desligar apaga a fila e impede novos lotes; uma requisição em andamento pode terminar. Relatos anteriores permanecem no GitHub.

Com autorização, diagnostic-pending.json guarda até 50 eventos permitidos. Somente lotes com falhas são enviados, por HTTPS sem redirecionamento. Incluem versão, sistema, eventos e um ID derivado dos IDs aleatórios dos eventos; não existe identificador permanente da instalação. Campos livres são descartados. O lote só é removido após confirmação de persistência pelo receptor. Eventos novos durante a requisição são preservados. Falhas de rede têm espera crescente de até uma hora sem bloquear a interface.

Endpoint: https://mktcommarcos.com.br/api/mark-mouse-teclado/diagnostics. Também existe o botão manual para revisar um resumo e abrir uma issue no navegador.

## Receptor

Código em services/diagnostics_receiver.py, container isolado markcontrol-diagnostics na VPS nova. Fonte, dados e segredo ficam em /opt/sites/mark-mouse-teclado-diagnostics. O proxy encaminha exclusivamente a rota acima com limite de 64 KiB e registro de acesso desativado. O container não publica portas no host e usa programas somente para leitura, usuário sem privilégios e limites de recursos.

A credencial do GitHub fica somente no arquivo protegido do servidor, montado como /run/secrets/github-token. Não faz parte da imagem, do app ou do repositório. O receptor valida o esquema antes de persistir em SQLite. HTTP 202 confirma persistência; o trabalhador tenta encaminhar uma assinatura por minuto. Assinaturas iguais são agrupadas. O ID do lote impede duplicação por reenvio. A busca por título recupera POSTs com resultado incerto considerando as últimas 100 issues.

Limites: 20 requisições por endereço/hora, 2.000 lotes/dia e 5.000 assinaturas. IPs são usados somente em memória para limitar abuso. Registros expiram após 30 dias desde a última ocorrência, incluindo pendências não encaminhadas. Issues públicas não são apagadas automaticamente. Falhas de entrega mantêm a fila e registram somente o tipo da exceção.

GET em .../diagnostics/health verifica saúde. Para comprovar entrega, consultar signature, occurrences, issue na tabela reports em reports.sqlite. Não expor banco ou segredo por HTTP. O backup da configuração original do proxy está em proxy-host9-before.json no diretório do serviço. Os demais serviços e rotas não são substituídos pelo receptor.

## Minimização

A janela é escondida antes da notificação. Falhas no aviso são registradas sem diálogo modal. Sem bandeja, a janela é minimizada para a barra de tarefas. Isso elimina um bloqueio possível no código; reproduzir no notebook afetado ainda é necessário para confirmar o relato.

Verificar com e sem sessão ativa os eventos window_hidden_in_tray, window_minimized_without_tray, tray_failed, tray_update_failed, tray_notification_failed e ui_callback_failed. Atualizar os dois PCs: versões antigas não contêm o pedido de autorização.
