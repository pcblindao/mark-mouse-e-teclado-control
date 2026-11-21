# Modo administrador e teclas especiais — 0.7.2

## Problema relatado

Em certas janelas do PC acessado o clique não funcionava, e os botões de teclas especiais da barra do visualizador pareciam não fazer nada.

## Causas

1. **UIPI do Windows.** O app rodava sem privilégio. O Windows descarta em silêncio cliques e teclas que um processo comum envia a janelas de processos elevados: Gerenciador de Tarefas, instaladores, Editor do Registro, Serviços e qualquer programa aberto com "Executar como administrador".
2. **Nomes das combinações.** O menu "Teclas especiais" do visualizador enviava `Meta`, `Control`, `Escape`, `d`, `l`, mas o cliente só aceita `Win`, `Ctrl`, `Esc`, `D`, `L`. Win, Win+D, Ctrl+Shift+Esc e Bloquear tela eram recusados antes de sair do PC ("Combinação inválida"). Alt+F4 era aceito pelo protocolo mas não aparecia no menu.

## Correções

- O executável Windows sempre roda elevado. Aberto sem privilégio por um administrador, ele se reabre pedindo o UAC; se o aviso for recusado, mostra a explicação e encerra. Se o app já estiver aberto, só mostra a janela existente, sem UAC.
- Usuário padrão do Windows (sem conta de administrador) roda sem elevação, no próprio perfil: pedir a senha de outra conta levaria o app para o perfil errado.
- Início automático antes da tarefa existir (primeiro logon após atualizar da 0.7.1): o agente sobe na hora, sem esperar ninguém responder ao UAC, e pede a elevação em segundo plano.
- Comandos de console (`scan`, `--help`) não pedem elevação.
- O início com o Windows passa a ser a tarefa agendada `Mark Mouse e Teclado Control - <usuário>` (uma por usuário do Windows) com "Executar com privilégios mais altos" e logon interativo: sobe elevado no logon sem novo aviso do UAC. A antiga entrada em `HKCU\...\Run` é removida na primeira abertura elevada. Desligar "Ativar ao iniciar" apaga a tarefa; o desinstalador também.
- Atualização a partir da 0.7.1: a nova versão confirma a saúde ao atualizador sem elevação (exigência da troca segura) e, em seguida, se reabre como administrador.
- O agente avisa o visualizador (`{"t": "notice", "code": "admin_window"}`, no máximo a cada 5 s) quando um clique ou tecla vai para uma janela elevada que ele não consegue controlar. Visualizadores antigos ignoram a mensagem.
- Menu de teclas especiais usa exatamente os nomes do protocolo e inclui Alt+F4. As teclas da combinação são pressionadas em ordem com 30 ms entre elas e soltas em ordem inversa.

## Limites que continuam

A confirmação do UAC e a tela de Ctrl+Alt+Del rodam na área de trabalho segura do Windows; nenhum programa de usuário, mesmo elevado, envia entradas para ela.

## Segurança

A tarefa elevada executa o binário da pasta de instalação por usuário (`%LOCALAPPDATA%\Programs\MarkControl`). Quem já consegue gravar nessa pasta com a conta do usuário passa a ter um caminho para rodar código elevado no próximo logon. O binário continua sendo substituído apenas pelo atualizador com manifesto assinado.

## Validação

```
python -m pytest -q tests/test_elevation.py
```
