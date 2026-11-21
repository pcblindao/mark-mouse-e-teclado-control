# Funcionalidades

| Área | Funcionalidade | Estado |
| --- | --- | --- |
| Tela | Captura o desktop em uma thread e envia quadros JPEG por WebSocket para o Tk ou o canvas web. | v0.1 |
| Tela | Envia quando o quadro muda. Se a imagem ficar igual, atualiza a cada dois segundos. | v0.1 |
| Monitores | Seleção de monitor numerado com mapeamento do mouse à região ativa | v0.1 |
| Qualidade | JPEG de 30 a 95 no protocolo, com seletor de níveis no visualizador desktop e na página web | v0.7 |
| FPS | O protocolo aceita de 1 a 60 FPS (5 a 60 no visualizador, conforme o PC remoto). Indicador de quadros recebidos. | v0.7 |
| Latência | Indicador a partir de ping e pong do WebSocket | v0.1 |
| Mouse | Usa coordenadas absolutas normalizadas. Aceita cliques, arraste e rolagem vertical e horizontal proporcional ao touchpad. | v0.7.2 |
| Teclado | Envia pressionamento, repetição ao segurar e soltura das teclas permitidas, `Ctrl` com sinais e teclado numérico, e acentos/ç como texto Unicode (desktop e web). | v0.7.2 |
| Texto | A caixa de texto envia até 500 caracteres ao PC remoto, sem inverter maiúsculas quando o Caps Lock remoto está ligado. | v0.7.2 |
| Área de transferência | Texto, imagens (até 32 MiB) e arquivos/pastas (até 512 MiB) nos dois sentidos, com `Ctrl+C`, `Ctrl+X`, `Ctrl+V` e botões. Copiar e colar dentro do próprio PC remoto mantém os formatos originais. Erros mostram o motivo. | v0.7.2 |
| Área de transferência | Pode ser desativada no PC controlado. Os diagnósticos não guardam o conteúdo. | v0.1 |
| Descoberta | Broadcast UDP IPv4 e listagem de nome, IP, porta HTTPS e SHA-256 | v0.1 |
| Rede | Aceita apenas os IPs privados e de loopback permitidos. Usa HTTPS e WSS na rede local. | v0.1 |
| TLS | Certificado autoassinado em pasta do usuário e SHA-256 no console | v0.1 |
| Autenticação | O dono pode definir um PIN, armazenado com scrypt e sal. As chaves dos aparelhos podem ser revogadas e há limite de tentativas por IP. | v0.1 |
| Sessão | Aceita uma conexão de controle por vez. O token tem 32 bytes e a sessão termina após 30 minutos sem uso. | v0.1 |
| Aviso de sessão | O ícone e a dica da bandeja indicam a sessão ativa e o IP de quem controla. | v0.5 |
| Encerramento | Use o botão Desconectar, `Ctrl+Alt+Shift+Q` no PC controlado ou `Ctrl+C` no console (modo `agent`). | v0.1 |
| Segurança do protocolo | Validação de tipos, campos, tamanhos, coordenadas e teclas | v0.1 |
| Privacidade | Visualizador e dados locais, sem dependências CDN. Diagnósticos técnicos só com autorização. | v0.5 |
| CLI | `markcontrol`, `markcontrol agent`, `markcontrol scan` e `python -m markcontrol`, com ajuda na linha de comando. | v0.1 |
| Opções do servidor | `--host`, `--port`, `--name`, `--cert-ip`, `--no-clipboard` | v0.1 |
| Opções de descoberta | `--broadcast` e `--timeout` | v0.1 |
| Plataformas | Windows 10/11 e Linux com X11. Em Wayland, avisa e encerra. | v0.1 |
| Janela | Tema escuro, botão de ativação local e "Ativar ao iniciar" (ativa este PC e o inicia no logon). Os ajustes ficam no perfil do usuário. | v0.7.2 |
| Descoberta contínua | Mostra nome, IP e situação (livre ou em uso). Atualiza a cada 3 s e remove PCs sem resposta após 10 s. | Rodada 2 |
| Cliente Tk | Conecta pelo PC selecionado. Permite ajustar qualidade, FPS e monitor e usar mouse, teclado, texto e área de transferência. | Rodada 2 |
| Identidade | Usa confiança na primeira conexão (TOFU) com SHA-256 em HTTPS e WSS. Se a identidade mudar, pede confirmação. | Rodada 2 |
| Distribuição | Build PyInstaller de arquivo único por plataforma | Rodada 2 |
| Gravação | Gravação local iniciada explicitamente | planejado |
| Sessões | Múltiplas sessões simultâneas | planejado |
| Áudio | Reprodução do áudio remoto | planejado |
| Arquivos | Envio de até 2 GiB para a pasta Downloads real do usuário › `Mark Mouse e Teclado Control`, com SHA-256 e nomes exclusivos. Clicar de novo em Enviar arquivo cancela. O visualizador mostra o resultado e o PC remoto avisa onde salvou. | v0.7.2 |
| Somente ver | Bloqueia mouse, teclado, texto, combinações de teclas e escrita na área de transferência durante a sessão. | Servidor e cliente |
| Teclas especiais | `Win`, `Win+D`, `Win+L`, `Alt+Tab`, `Ctrl+Shift+Esc`, `PrintScreen` e `Alt+F4` | Servidor e cliente |
| Ctrl+Alt+Del | Depende de um serviço do Windows rodando como SYSTEM e autorizado a emitir a sequência segura. | Planejado |
| Mensagens | Aceita até 500 caracteres por mensagem e permite resposta no PC controlado. | Servidor e cliente |
| Wake-on-LAN | Envio do pacote mágico implementado internamente; ainda sem botão na interface. | Em breve |
| Histórico | Guarda as últimas 500 sessões, com horário, IP, duração em segundos e funções usadas. | Servidor |
| Administrador | No Windows o app sempre roda como administrador e controla janelas elevadas. Início no logon por tarefa agendada por usuário. | v0.7.2 |
| Atualização | Sempre automática: verifica ao abrir e a cada 15 min; se houver sessão, encerra, atualiza e volta ativado. Configurações, PIN e pareamentos são preservados. | v0.7.2 |
