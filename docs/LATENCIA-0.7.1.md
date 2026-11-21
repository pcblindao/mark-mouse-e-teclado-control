# Latência de entrada — 0.7.1

## Evidência recebida e hipótese

Os diagnósticos reais anteriores mostraram no controlador 0.7: resolução 1366×768, decodificação de 5–8 ms, RTT de 16–187 ms e amostras de 1–8 FPS. O servidor 0.6 registrou captura de 44–47 ms e envio de 0–1 ms. FPS de 1–2 em tela parada não mede o teto da transmissão: quadros iguais são suprimidos e há atualização periódica.

Esses dados não provam uma única causa física. Como a decodificação estava relativamente curta e o atraso variava durante entrada, foi auditada a fila dos comandos. Cada evento de movimento era transmitido e aplicado individualmente. Uma chamada nativa lenta de mouse podia acumular posições antigas antes de cliques e teclas.

## Alterações

- Cliente substitui somente movimentos consecutivos pelo mais recente. Cliques, teclas, rolagem, arraste e soltura formam barreiras ordenadas.
- Movimento contínuo tem teto de 60 envios/s. Um comando interrompe a espera de movimento e força a posição mais recente antes da barreira, para preservar a ordem.
- Servidor separa leitura WSS de execução dos comandos, com fila limitada a 128 itens e a mesma coalescência. Não há mudança de thread dos controladores `pynput`, preservando as regras de teclas, botões e clipboard.
- A fila do cliente é limitada a 512 itens. Movimentos redundantes são substituídos; sob excesso de comandos não substituíveis a sessão é encerrada e registrada, acionando a liberação das teclas/botões no servidor em vez de crescimento ilimitado ou tecla presa.
- Blocos binários de arquivo são enfileirados e validados na execução, depois do respectivo cabeçalho, preservando a ordem de início/bloco/finalização.
- `input_stats` registra somente duração média, quantidade de movimentos aplicados, movimentos descartados e comandos. Não registra coordenadas, teclas, texto ou nomes de arquivos.

## Ensaio reproduzível

Com as dependências de teste instaladas:

```
python scripts/benchmark_input_queue.py
python -m pytest -q tests/test_input_latency.py
```

O ensaio usa TLS/WebSocket reais em loopback, captura sintética e entrada falsa que demora 20 ms por operação. Nenhuma área de transferência, captura da área de trabalho ou entrada física é usada. O controlador envia 200 movimentos, pressionamento do mouse, mais 200 movimentos, soltura do mouse e pressionamento/soltura de uma tecla. O caminho anterior executa os 400 movimentos; o novo mantém os dois pontos finais e os quatro comandos, na ordem correta.

| Caminho | Movimentos aplicados | Comandos preservados | Até a soltura final |
| --- | ---: | ---: | ---: |
| Leitura serial anterior, entrada síncrona de 20 ms | 400 | 4 | 8.476,9 ms |
| Fila com coalescência, entrada síncrona de 20 ms | 2 | 4 | 136,2 ms |
| Fila com coalescência, entrada assíncrona de 20 ms | 2 | 4 | 138,5 ms |

Há também testes para o teto de movimento contínuo, interrupção por soltura de tecla e fechamento seguro sob excesso de comandos.

## Limites

Os números acima medem um surto sintético específico. Não representam RTT garantido ou tempo total entre os dois computadores. A captura real de 44–47 ms continua sendo um limite separado, assim como Wi-Fi, carga do Windows, processamento e resolução escolhida. A confirmação no notebook exige atualizar os dois lados e comparar novos registros durante uso real.
