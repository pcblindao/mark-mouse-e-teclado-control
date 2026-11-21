# Auditoria e atualização 0.6.0

## Resultado e evidência

O caminho de imagem da v0.5.1 decodificava JPEG e redimensionava na mesma rotina assíncrona que enviava os comandos. A fila de eventos também misturava quadros com controles. Agora há decodificação fora do loop de rede, uma fila de um quadro pronto e um único JPEG pendente; quadros intermediários não acumulam atraso. O remetente de comandos é acordado por evento, eliminando a espera fixa de 10 ms. A captura usa um trabalhador exclusivo, reutiliza o dispositivo e usa SHA-256 acelerado para detectar mudanças.

`scripts/benchmark_stream.py` compara os caminhos antigo e novo com imagens sintéticas, intercalando as medições. Os números medem processamento e espera do loop de eventos, não a latência total de uma rede. Não capturam a tela do usuário. A evidência inicial está no relatório de trabalho; o script permite repetir em outro PC.

O teste WSS usa TLS, Client e Agent reais em loopback, com captura e dispositivos de entrada sintéticos. Confirma entrega de comandos enquanto o decoder está bloqueado, mudança de monitor, coordenadas originais e recuperação da imagem. Testes de unidade também verificam limite das filas e ausência de ampliação artificial da imagem.

## Transmissão

- Até 60 FPS, com padrão de 30; o resultado depende de hardware, resolução e rede.
- Resolução automática adequada à janela, original e opções 720p/1080p/1440p/2160p. Não amplia além do monitor remoto. Tamanho real usa transmissão original.
- JPEG 4:4:4 preservado para legibilidade de texto. A decodificação pode usar redução nativa do JPEG antes do ajuste final quando a janela é menor.
- Clientes antigos continuam usando o comando de qualidade anterior; os novos só pedem os recursos anunciados pelo servidor.
- O dispositivo de captura é fechado no trabalhador correto ao encerrar a sessão.

## Ferramentas revisadas

Os testes cobrem descoberta, PIN/pareamento, certificado, sessão única, mouse/teclado, texto, área de transferência com limite de 100 KiB, modo somente visualizar, teclas especiais, transferência de arquivos com integridade e cancelamento, chat, Wake-on-LAN e histórico local. O chat do PC controlado não era tratado pela interface; agora é consultado em Configurações → Mensagens da sessão e só confirma resposta depois da entrega. Não abre janelas espontaneamente. A permissão de transferência também está nas Configurações.

Ativo é uma preferência persistente. Ativar configura início com o sistema; o início automático fica em segundo plano. O agente tenta restabelecer o serviço após falha. Desativar e Sair continuam disponíveis. Uma configuração recuperada de erro exige revisão antes de ativar.

Foram removidas notificações de rotina ao recolher a janela. O ícone de sessão ativa permanece visível e oferece controle ao usuário.

## Atualização automática

A v0.5.1 não habilitava o atualizador fora da pasta de instalação e tinha a chave pública vazia. A v0.6.0 inclui uma chave Ed25519 real, funciona no executável direto e verifica novidades no início e a cada 15 minutos, quando habilitada. Falhas são registradas e recebem nova tentativa. Sessões em andamento impedem a troca. A atualização verifica assinatura, tamanho, hash, saúde do candidato e restaura o anterior em caso de falha; preserva a intenção Ativo e o estado da janela.

A chave privada de assinatura fica fora do repositório e foi configurada como segredo criptografado do GitHub Actions. Somente a chave pública é distribuída. Cada release publica executável e manifesto assinado para Windows e Linux.

O teste de processos construiu dois executáveis Windows sem GUI em uma pasta isolada e executou quatro cenários: troca real, rollback por falha do candidato, cancelamento e recusa de adulteração após a assinatura. Também verificou que uma sessão impede armar a troca. Não alterou o aplicativo instalado nem PCs de colaboradores.

A v0.5.1 e anteriores precisam baixar a v0.6.0 uma vez: não é possível corrigir remotamente a chave ausente no executável antigo. A partir daí, o atualizador assinado assume as versões futuras. Falta de internet, bloqueio do sistema ou falta de permissão continuam sujeitos a registro e nova tentativa.

## Logs e limites

Consulte DIAGNOSTICOS.md. A coleta ampliada exige autorização nova (versão 2), incluindo eventos de sessão e métricas numéricas, e funciona sem uma exceção. O recebimento HTTPS persiste o lote antes de confirmar. O receptor agrupa eventos normais por versão/sistema/dia, atualiza o diagnóstico mais recente no GitHub e conta lotes; falhas são agrupadas pela assinatura técnica.

A validação automatizada não substitui um teste físico no notebook afetado e na rede da empresa. Desempenho real de Wi-Fi, permissão para entrada em janelas elevadas, áudio e periféricos físicos não foi comprovado pelos dispositivos sintéticos. A versão não adiciona gravação de tela ou coleta de conteúdo aos logs.
