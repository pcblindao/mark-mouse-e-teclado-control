# Área de transferência e instalação única — 0.7.0

## Uso no aplicativo desktop

A sessão oferece copiar/colar de texto, prints e imagens, arquivos e pastas nos dois sentidos. Use Ctrl+C/Ctrl+V na tela remota ou os botões Copiar do remoto e Colar no remoto. O botão Copiar busca o clipboard atual do outro PC; o atalho Ctrl+C também executa a cópia na aplicação remota. Em seguida o conteúdo pode ser colado em outro aplicativo do computador local.

Os dois PCs precisam de uma versão com clipboard_rich anunciado. Com versões antigas, o caminho de texto anterior continua disponível. A área de transferência deve estar permitida nas Configurações dos dois lados; arquivos também exigem a permissão de transferência. Modo somente visualizar bloqueia alteração de clipboard e simulação de atalhos, mas permite ler o clipboard já existente quando autorizado.

Suporte nativo Windows: texto Unicode, PNG/DIB/DIBV5 e CF_HDROP com operação de cópia. Imagens são normalizadas como PNG; não há promessa de transportar formatos privados de aplicativos, objetos OLE ou toda a formatação proprietária. Linux X11 usa xclip; Wayland continua fora do suporte. O navegador mantém o caminho de texto anterior.

Limites: texto100KiB, imagem32MiB/32MP e seleção de arquivos/pastas512MiB no total, até256entradas incluindo pastas. Links, junctions, nomes incompatíveis e árvores profundas são recusados. O envio de arquivo individual maior continua disponível pelo botão de transferência, conforme o limite próprio desse recurso.

## Transporte e integridade

O endpoint HTTPS /api/clipboard exige uma sessão WSS ativa, token vinculado à sessão e mesmo IP. Há verificação de Origin, fingerprintTLS, opt-out, somentevisualizar e permissão de arquivos antes e durante a operação. O limite global de login continua1KiB; bundles são transmitidos em blocos com limite real separado.

O pacote é ZIP_STORED com manifesto e hashes; as entradas internas são indexadas e nunca viram caminhos de extração diretamente. Antes de alterar o clipboard, o receptor valida tamanhos, hashes, entradas, caminhos e integridade. Não usa extractall, não segue links e não sobrescreve arquivos originais. Os arquivos recebidos ficam numa transação única no diretório privado clipboard-received do perfil para que o sistema operacional possa colá-los depois. Não há limpeza automática dessas transações nesta versão.

Empacotamento, leitura, escrita e operações nativas ficam fora do loop da mídia e da interface. Cancelar aguarda o worker parar antes de remover temporários. Os atalhos preservam modificadores físicos já pressionados. Não existe sincronização indiscriminada em segundo plano: a operação é iniciada pelo comando de copiar/colar.

O conteúdo segue somente entre os dois PCs pela sessão. Não é enviado ao receptor de diagnósticos ou ao GitHub. Logs de erro guardam tipo e frames de código, sem textos, imagens, arquivos, nomes ou caminhos do clipboard.

## Instalação Windows

O executável instala no perfil do usuário em %LOCALAPPDATA%\Programs\MarkControl\MarkMouseTecladoControl.exe. Novas instalações substituem o binário canônico; abrir novamente conversa com a instância existente por IPC local autenticado, sem criar outra janela do aplicativo. Uma trava serializa instalações concorrentes. Versão e hash impedem que um instalador antigo sobrescreva uma cópia mais nova.

O perfil %LOCALAPPDATA%\MarkControl permanece no mesmo lugar. Configurações, Ativo, consentimento de diagnósticos v2, identidadeTLS, confiança/pareamentos e histórico são preservados. Não há nova autorização para a mesma preferência já aceita. A instalação só gerencia seus próprios arquivos de programa, trava e metadados.

A migração de executáveis0.5.1/0.6.0 em execução exige mesmo usuário e hash/tamanho oficiais, com handle do processo correspondente. Não encerra processos por nome genérico; se não puder encerrar uma cópia comprovada, não inicia outra. O helper previous.exe não é alvo. Caminhos legados comprovados são atualizados para que atalhos antigos usem o bootstrap novo.

Na transição pelo atualizador0.6.0, RESUME mantém o processo/caminho do candidato até confirmar saúde, evitando rollback falso. A mudança para o caminho canônico ocorre numa abertura normal posterior. Não altera assinatura, reversão automática ou preferências.

## Validação

Testes de unidades usam backends de clipboard falsos; não leem o clipboard real do desenvolvedor. Testes TLS reais cobrem texto/PNG/arquivos/pasta vazia nos dois sentidos, sessão/token/IP/Origin, permissões, revogação, integridade e comandos durante empacotamento bloqueado. Testes de ZIP incluem traversal, links, arquivos comprimidos e metadados gigantes.

A instalação tem testes de instância única, IPC autenticado, substituição, versão mais nova e preservação byte a byte dos arquivos de configuração/aceite. Quatro cenários com executáveis Windows reais, sem GUI, cobrem sucesso, rollback, cancelamento e adulteração em integração com o bootstrap. O CI Windows também instala o executável, recolhe a janela e abre novamente para confirmar uma única janela existente.

Clipboard físico entre aplicativos reais e os dois computadores da empresa ainda precisa de validação no ambiente do usuário; os testes não substituem esse passo.
