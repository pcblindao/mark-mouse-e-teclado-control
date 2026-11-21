# Diagnósticos — 0.6.0

A autorização é opcional e versionada. A versão 2 abrange diagnósticos técnicos durante o uso e em falhas: eventos de sessão/atualização, resolução, FPS, tempos de captura/decodificação/envio/RTT e tipo/localização de exceções. Recusar mantém os recursos. Preferência anterior de versão1 não concede autorização para o escopo ampliado. No início sem janela, a pergunta só aparece quando o usuário abre o aplicativo.

Não entram nos relatórios tela, teclas, PIN, tokens, texto da área de transferência/chat, arquivos, nome de PC, IP de conexão ou caminhos completos. O esquema permite somente nomes fixos de evento, métricas inteiras permitidas e frames de código sem texto da exceção ou variáveis. Não há identificador permanente de instalação.

Há quatro registros locais de até 256 KiB. A fila de envio contém até 50 eventos, com lotes de até25; campos inválidos não bloqueiam novos registros. Há envio por atividade relevante ou falha, sem exigir exceção. O remetente começa após30s e normalmente confere a fila a cada5min; em falhas usa espera crescente de até1h. A fila só remove os IDs confirmados, preservando eventos novos. Desligar apaga a fila e impede próximos lotes; uma requisição em andamento pode terminar. Publicações anteriores permanecem no GitHub.

Endpoint: https://mktcommarcos.com.br/api/mark-mouse-teclado/diagnostics. O receptor isolado na VPS nova exige JSON estrito de até64 KiB, persiste SQLite antes de responder202 e limita abuso. O endereço IP é usado somente em memória para essa limitação; o registro de acesso da rota está desligado. Há limite de20 requisições por endereço/hora, 2.000 lotes/dia e 5.000 assinaturas. Registros expiram30dias após a última ocorrência, incluindo pendências. O token de GitHub existe somente no arquivo protegido do servidor, montado apenas para o receptor.

Atividade normal é agrupada por versão/sistema/dia; falhas por assinatura técnica. O trabalhador publica ou atualiza um grupo por minuto e mantém o último diagnóstico e a contagem de lotes no GitHub. Reenvios com o mesmo ID não duplicam contagens. published_at permite retomar atualizações pendentes depois de reiniciar; a migração preserva registros já entregues.

Fonte: services/diagnostics_receiver.py. Serviço markcontrol-diagnostics, dados em /opt/sites/mark-mouse-teclado-diagnostics/data/reports.sqlite. O container não publica portas no host, usa usuário sem privilégios, programa somente para leitura e limites de recursos. Saúde em .../diagnostics/health; receiver_schema=2 identifica esta versão. A tabela reports permite conferir issue, occurrences, last_seen e published_at sem expor o banco por HTTP. As credenciais e backups não pertencem ao diretório público do site.

A minimização tem testes com sessão ativa e não usa aviso modal nem notificação de rotina. O caso do notebook mostrado pelo usuário correspondia à interface antiga que restaurava a janela durante sessão; ele precisa executar a versão atual.
