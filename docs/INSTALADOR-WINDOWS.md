# Instalador Windows

O download principal do Windows é `MarkMouseTecladoControl-Setup-<versão>.exe`, gerado pelo Inno Setup 6. O executável avulso e seu manifesto `.sig` continuam sendo os anexos usados pela atualização automática; o atualizador não abre o assistente de instalação.

## Identidade e arquivos

- AppId permanente: `{F7063A58-BC52-46D8-B1CE-479E87DF7281}`. Não trocar esse identificador em versões futuras.
- Produto: Mark Mouse e Teclado Control. O assistente e a entrada em Aplicativos Instalados exibem a versão.
- Instalação somente para o usuário atual, sem elevação: `%LOCALAPPDATA%\Programs\MarkControl\MarkMouseTecladoControl.exe`.
- A pasta é fixa e não há página para escolher outro diretório. Uma opção `/DIR` divergente dessa pasta no ambiente é recusada.
- Menu Iniciar e atalho na área de trabalho usam nomes permanentes, sem a versão, para substituírem os anteriores.
- Configurações, consentimentos, identidade, pareamentos e histórico permanecem em `%LOCALAPPDATA%\MarkControl`, fora da pasta de programas. O instalador e o desinstalador não removem esse perfil.

O assistente não solicita pasta, grupo ou atalhos. As autorizações já salvas permanecem; permissões necessárias do Windows e consentimentos ainda pendentes continuam disponíveis.

## Sequência de substituição

1. O mutex `Local\MarkMouseTecladoControl-Setup` serializa instalações e impede que uma nova janela do app abra no intervalo de substituição.
2. Antes de copiar, o Setup extrai uma cópia temporária do próprio executável e executa `--prepare-install`, sem GUI. O helper solicita o encerramento da instância existente pelo IPC autenticado e trata somente cópias legadas comprovadas do produto. Falha interrompe a instalação.
3. O Inno Setup substitui o executável no mesmo diretório e mantém uma entrada única de desinstalação. O Restart Manager também acompanha o arquivo do aplicativo.
4. O executável canônico recebe `--register-install` para atualizar os metadados e os caminhos legados comprovados. Erro impede confirmar a instalação como concluída.
5. O Setup inicia `--after-install --autostart`: essa passagem reconhece a instalação em andamento e permite uma única instância. A opção Ativo salva determina a retomada; o consentimento de diagnósticos v2 é preservado.

O desinstalador solicita o mesmo encerramento por uma cópia temporária do helper, sem manter o executável canônico aberto enquanto espera liberá-lo. Também mantém o mutex durante a remoção. Remove apenas arquivos de instalação registrados, metadados próprios, backup próprio e a pasta própria de atualização; não apaga preferências e aceites.

## Build

Após compilar o executável da mesma versão do código:

```powershell
./scripts/build-installer.ps1 -AppExe ./dist/markcontrol-windows-x64.exe
```

São aceitos `-OutputDir`, `-PythonExe` e `-IsccExe`. O script procura o Inno Setup nos locais padrão e no PATH; não instala ferramentas. Usa o Python do ambiente de build, com PyInstaller, para conferir a versão embutida e a existência dos comandos do helper antes de gerar o Setup. O fallback do executável é `dist/Mark Mouse e Teclado Control.exe`.

## Validação isolada

Compilar o Setup no PC de desenvolvimento não instala o programa. Não executar o instalador real no perfil do Marcos durante testes: mesmo com uma pasta temporária, atalhos e a entrada de desinstalação usam o registro e as pastas do usuário do Windows.

Na CI Windows, usar um perfil de dados isolado e a pasta canônica correspondente. Para evitar abrir o app automaticamente durante a preparação do teste, passar `/NOSTART` junto com `/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /SP-`. Depois abrir explicitamente o aplicativo para os testes.

Validar instalação inicial e reinstalação com a cópia anterior aberta; conferir `unins000.exe`, a entrada única em HKCU e o destino do atalho. Comparar os bytes dos arquivos de preferências/aceites antes e depois. Dupla abertura deve produzir um único dono do IPC e uma única janela; o executável PyInstaller onefile normalmente tem um processo carregador e outro do aplicativo, o que não significa duas janelas.
