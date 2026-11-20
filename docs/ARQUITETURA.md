# Arquitetura

## Componentes

```mermaid
flowchart LR
    subgraph LAN["Rede local"]
        V["Cliente Tk com aiohttp ou navegador"]
        GUI["Janela Tk"] --> R["AgentRunner em thread"] --> H
        GUI --> S
        S["Busca contínua ou pela CLI"]
        subgraph A["PC controlado"]
            H["server"]
            G["security"]
            C["capture"]
            I["inputctl"]
            P["clipboard"]
            D["discovery"]
            T["certs"]
            OS["Sessão gráfica e área de transferência locais"]
            H --> G
            H --> C
            H --> I
            H --> P
            H --> T
            D --> T
            C --> OS
            I --> OS
            P --> OS
        end
        V <-->|"HTTPS e WSS"| H
        S <-->|"UDP IPv4 47800"| D
    end
```

## Login e WebSocket

```mermaid
sequenceDiagram
    actor Dono
    participant V as Visualizador
    participant A as Servidor
    Dono->>A: Ativa o controle no desktop
    V->>A: No cliente nativo, confere o certificado HTTPS com a impressão da descoberta ou a já salva
    V->>V: Salva a identidade verificada ou pede confirmação se ela mudou
    V->>A: POST HTTPS /api/login com {}, PIN ou device_key
    A->>A: Confere o IP, o limite por IP e a credencial configurada
    A-->>V: Token vinculado ao IP e chave de pareamento, quando houver
    V->>A: Upgrade WSS /ws com subprotocolos markcontrol e token
    A->>A: Consome o token e confere se já existe uma sessão
    A-->>V: HTTP 101 com subprotocolo markcontrol
    A-->>Dono: SESSAO ATIVA de IP
```

## Captura da tela

```mermaid
sequenceDiagram
    participant A as Servidor
    participant T as Thread de captura
    participant V as Visualizador
    A->>T: Informa o monitor, a qualidade e o hash anterior
    T->>T: Captura pixels e calcula hash
    alt A geometria, a qualidade ou os pixels mudaram, ou passaram dois segundos sem quadro
        T->>T: Codifica JPEG
        T-->>A: Geometria e JPEG
        opt Geometria nova
            A-->>V: JSON info
        end
        A-->>V: Mensagem binária JPEG
        Note over V: No cliente nativo, a thread do cliente decodifica e redimensiona a imagem para o canvas. O Tk exibe o quadro.
    else Pixels iguais
        T-->>A: Sem JPEG
    end
```

## Área de transferência

```mermaid
sequenceDiagram
    participant V as Navegador
    participant A as Servidor
    participant P as Thread da área de transferência
    alt Colar no remoto
        V->>V: navigator.clipboard.readText
        V->>A: clip_set com texto UTF-8
        A->>A: Valida sessão, opção e tamanho
        A->>P: pyperclip.copy
        A-->>V: clip_ok
    else Copiar do remoto
        V->>A: clip_get
        A->>A: Valida sessão e opção
        A->>P: pyperclip.paste
        P-->>A: Texto UTF-8 com tamanho validado
        A-->>V: clip com texto
        V->>V: navigator.clipboard.writeText
    end
    Note over A,P: O log registra a operação e a quantidade de bytes
```

## Descoberta

```mermaid
sequenceDiagram
    participant S as Scanner
    participant A as PC controlado
    S->>A: Broadcast IPv4 MARKCONTROL? em UDP 47800
    A->>A: Confere o pacote e o IP de origem
    opt Origem privada permitida
        A-->>S: Unicast com name, port, fingerprint, busy e pin_required
        S->>S: Confere a resposta e inclui o PC na lista
    end
```

## Camadas de segurança

```mermaid
flowchart TD
    N["Firewall com escopo de sub-rede"] --> I["IP privado da conexão direta"]
    I --> T["TLS e conferência da impressão digital"]
    T --> O["Origem do navegador"]
    O --> P["PIN opcional ou aparelho pareado e limite por IP"]
    P --> S["Token temporário e sessão única"]
    S --> M["Validação estrita de mensagens e limites"]
    M --> C["Entrada gráfica e área de transferência opcional"]
    K["Atalho local e expiração"] --> R["Encerra a sessão e solta teclas e botões"]
```

| Módulo | Responsabilidade | Arquivo |
| --- | --- | --- |
| CLI | Lê as opções e os comandos e confere a sessão gráfica. | `markcontrol/cli.py` |
| Servidor | Gerencia o ciclo de vida das conexões HTTPS e WSS e envia os quadros. | `markcontrol/server.py` |
| Segurança | Confere IP, PIN e token, aplica o limite de tentativas e valida as mensagens. | `markcontrol/security.py` |
| Certificados | Criação e carregamento de identidade TLS e impressão digital | `markcontrol/certs.py` |
| Descoberta | Envia consultas e responde por UDP IPv4. | `markcontrol/discovery.py` |
| Captura | Monitor, hash dos pixels e codificação JPEG | `markcontrol/capture.py` |
| Entrada | Controla mouse e teclado e registra o atalho global. | `markcontrol/inputctl.py` |
| Área de transferência | Lê e escreve em uma thread e aplica o limite de bytes UTF-8. | `markcontrol/clipboard.py` |
| Visualizador | PIN, canvas, comandos e estatísticas no navegador | `markcontrol/static/index.html`, `markcontrol/static/viewer.js` e `markcontrol/static/style.css` |
| Janela e controle local | Atualiza a descoberta de PCs, ativa o controle local e cuida da inicialização no login e da faixa obrigatória. | `gui/app.py`, `gui/autostart.py`, `agent_runner.py` |
| Cliente nativo | HTTPS/WSS com SHA-256 fixado, fila de eventos, quadros e entrada Tk | `client.py`, `gui/viewer.py` |
| Persistência | Guarda a configuração, o resumo scrypt do PIN e os resumos das chaves no PC controlado. O visualizador mantém `known_hosts`. | `config.py`, `trust.py` |

Por padrão, o scanner consulta o broadcast global e os endereços de broadcast das interfaces IPv4 privadas. Se só tiver o IP local, assume máscara `/24`. Para escolher o destino, use `scan --broadcast`. O scanner lista uma única resposta por IP.

O scanner completa a consulta `MARKCONTROL?` até 1280 bytes. Se a consulta for curta, o PC responde com `PAD 1280`. Cada consulta válida recebe no máximo um datagrama de resposta, com tamanho igual ou menor que o da solicitação. O scanner lê o IP de origem no socket UDP.

O cliente só salva a confiança depois de validar o certificado HTTPS. Ele recusa redirecionamentos e só envia a chave de pareamento ao servidor com a identidade salva.

## Extensões do protocolo

A mensagem `info` traz `features`, um objeto com valores booleanos para `files`, `view_only`,
`combo`, `chat`, `clipboard`, `wol` e `history`. Uma chave ausente indica recurso
indisponível. O sistema escolhe o MAC, que é opcional. Em PCs com mais de uma
interface, esse endereço pode ser de outra placa. O cliente pode guardar esse valor
junto à identidade verificada na integração com a interface.

* `Client.send_file(path, progress_cb)` inicia um envio após o primeiro `info`.
  O cabeçalho JSON `file_begin` contém `name` e `size` em bytes. O servidor responde
  `file_ready` ao cabeçalho e a cada mensagem binária de 256 KiB (a última pode ser
  menor). Arquivos vazios passam direto ao `file_end` com `sha256` hexadecimal.
  O servidor confere o tamanho e o hash antes de salvar o arquivo. Nomes repetidos recebem
  sufixos como ` (1)`. Em Linux, o volume de destino precisa aceitar hard links.
  O callback recebe `(bytes_confirmados, total)` na thread do cliente.
  `cancel_file()` solicita `file_cancel` antes de concluir o envio. A fila de
  eventos entrega `(file_ok|file_cancelled|file_error, None)`. Se a conexão cair,
  a sessão termina e o servidor descarta o arquivo parcial. A opção booleana `files`
  começa ligada e pode ser salva com `Config.save(..., files=False)`.
* `set_view_only(bool)` envia `view_only` com `enabled`. Ao ativar o modo, o servidor solta
  as teclas e os botões pressionados e confirma o estado por eco. A leitura da
  área de transferência segue a própria permissão. `send_combo(lista)` envia `combo` com `keys`.
  Cada elemento usa a grafia da tabela de funcionalidades (por exemplo,
  `["Ctrl", "Shift", "Esc"]`). Os efeitos dependem do desktop e do SO.
* `send_chat(texto)` envia `chat` com `s`. O servidor aceita uma mensagem por segundo
  e chama `notify("chat", {"text": texto, "reply": callback})`. A interface pode
  chamar `callback(resposta)` uma vez nessa sessão. O cliente recebe o evento
  `("chat", resposta)`. O histórico e o console omitem nomes de arquivo, conteúdo,
  teclas e credenciais.
* `wol.broadcasts()` mapeia broadcasts privados a IPs de interfaces locais.
  `wol.wake(mac, broadcast)` envia um datagrama UDP à porta 9 pela interface
  correspondente. O PC que controla faz o envio, inclusive com seu servidor local desligado.
* `history.json` fica ao lado de `config.json`. A gravação usa lock, troca atômica
  e arquivo temporário privado (0600 em POSIX, ACL herdada do perfil em Windows).
  `started` é UTC em ISO 8601 e `duration_s` usa o relógio monotônico.
