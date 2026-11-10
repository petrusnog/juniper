---
title: Reconhecer quem fala
group: Documentação Técnica
section: Clientes
order: 29
description: Como a Juniper aprende a voz de quem se cadastrou, onde guarda esse dado biométrico e como o protege, como cadastrar, testar e apagar uma voz, e o que a voz reconhecida pode e não pode decidir.
status: proposto
tags: voz identidade speaker biometria voiceprint embedding cadastro consentimento sherpa-onnx cifra aes-gcm limiar privacidade adr-034
---

> **Nota:** esta página detalha a seção 3 do [ADR-034](../decisions/ADR-034.md). Código: `brain/voice/speaker.py`, `brain/clients/voices_cli.py` e, na sessão de voz, `brain/clients/voice_session.py`. **Estado:** o cadastro, o cofre, a comparação e a política «só vozes conhecidas» estão prontos e testados com um extrator de mentira; **nunca rodou com um modelo de voz de verdade**, então o limiar certo e as taxas de erro são desconhecidos.

## Visão geral {#visao-geral}

Cada voz cadastrada vira um **vetor**: algumas centenas de números que um modelo calcula a partir do áudio. Duas gravações da mesma pessoa dão vetores que apontam para o mesmo lado (cosseno perto de 1); pessoas diferentes, não. Para saber de quem é uma fala, o cérebro calcula o vetor dela e compara com os cadastrados.

```text
gravações (WAV) ──► modelo ──► vetor ──► cifrado ──► MongoDB (voiceprints)
fala ──► modelo ──► vetor ──► cosseno com cada cadastrado ──► «é a Ana» · «ninguém conhecido»
```

## O que a voz decide, e o que não {#nao-e-autenticacao}

**Voz não é autenticação.** Ela decide se a Juniper *responde* e *como chama a pessoa*. Nunca levanta um tier, uma permissão ou uma confirmação ([ADR-006](../decisions/ADR-006.md), [ADR-010](../decisions/ADR-010.md)): uma gravação da voz do dono não ganha nada que o aparelho, com a credencial dele, já não tivesse. Ações que importam continuam presas à credencial do aparelho e à confirmação em conversa.

## Dado biométrico: como é protegido {#protecao kind=conceito file=brain/voice/speaker.py}

- **Cifrado em repouso.** O vetor vai ao MongoDB cifrado com AES-256-GCM, e a chave é um **arquivo do servidor** (`voice.speaker.key_file`, criado no primeiro cadastro com permissão 600), fora do banco. Quem leva só o banco não lê nada.
- **Preso ao próprio registro.** A cifra amarra o vetor ao id do cadastro, ao usuário, ao modelo e ao tamanho do vetor: mover ou editar o registro o torna ilegível, em vez de passar a valer para outra pessoa. Quem escreve no banco sem a chave ainda pode alterar os campos em claro (rótulo, data do consentimento, número de amostras); eles não são segredo, mas também não são prova.
- **Só com consentimento.** O cadastro exige o «sim» da pessoa (`--consent` ou a pergunta na tela) e guarda quem registrou e quando. O modelo de ameaça é o de um administrador local: quem tem acesso ao servidor pode passar `--consent` e usar a gravação de outra pessoa; o registro mostra quem rodou o comando, não prova que a pessoa concordou.
- **Sem áudio guardado.** As gravações são lidas, viram vetor e são descartadas.
- **Apagar é completo e vale em segundos.** O vetor some do banco na hora; o servidor guarda a lista em memória por até 5 s, então a voz apagada ainda pode ser reconhecida por esse tempo. Cópias antigas do banco (backups) ainda têm o vetor, **cifrado**, até serem descartadas; sem a chave, ele não se lê.
- **Nunca em log, cliente ou terceiro.** Só a semelhança (um número) pode ir ao log. A lista (`juniper voices list`) não mostra o vetor.
- **Perdeu a chave?** Os cadastros ficam ilegíveis e é preciso cadastrar de novo. **Registros que existem mas não se leem não contam como «ninguém cadastrado»**: em `enforce` a Juniper passa a não responder a ninguém (e o log diz `speaker_unreadable`, com a contagem), em vez de abrir para todos. Guarde um backup da chave **à parte** do backup do banco: o `backup_memory.sh` leva o banco, não a chave.
- **Onde a chave mora.** Em produção, no volume de estado (`/home/juniper/.juniper/state/voice/speaker.key`, definido em `docker/config.prod.yaml`); no sistema de arquivos do contêiner ela morreria a cada deploy. O servidor e a CLI precisam enxergar **o mesmo arquivo** (rode `juniper voices` dentro do contêiner). A chave é recusada se outros usuários podem lê-la (`chmod 600`).
- **Trocou de modelo?** Cada cadastro lembra qual modelo o gerou e não é usado (nunca misturado) se o modelo configurado for outro; conta como cadastro ilegível, ou seja, `enforce` fecha até recadastrar.

## Cadastrar, listar, testar e apagar {#cli kind=comando file=brain/clients/voices_cli.py}

Roda onde o servidor está (veja [Operação](operacao.md#atalho)). O usuário já tem de existir ([Usuários e credenciais](usuarios.md)).

```bash
juniper voices add Ana --wav ana1.wav --wav ana2.wav --wav ana3.wav --label normal
juniper voices list
juniper voices test gravacao.wav
juniper voices remove vp_1a2b3c4d5e6f
juniper voices forget Ana
```

- **`add`**: três gravações ou mais, WAV mono de 16 bits, de 1,5 a 30 s cada, da mesma pessoa falando normalmente. A Juniper pergunta se a pessoa concorda (ou use `--consent`). São recusadas amostras curtas, quase mudas, estouradas ou que **não pareçam a mesma voz** (o pior par de amostras precisa passar de 0,5 de semelhança). Uma pessoa pode ter mais de uma voz (`--label rouca`).
- **`list`**: id, pessoa, rótulo, número de amostras, modelo, quem consentiu e quando.
- **`test`**: diz de quem soa uma gravação, com a semelhança e a vantagem sobre o segundo colocado; serve para escolher o limiar.
- **`remove`** / **`forget`**: apaga uma voz ou todas as de uma pessoa, na hora.

## Só vozes conhecidas {#politica kind=conceito file=brain/clients/voice_session.py}

`voice.speaker.mode` escolhe o que o reconhecimento faz, e o padrão é não fazer nada:

| Modo | O que muda |
|---|---|
| `off` | Nada: não calcula o vetor, não consulta o cadastro. |
| `shadow` | Calcula e registra no log a semelhança (`speaker_shadow`: conhecida ou não, a pontuação, a vantagem), **sem mudar o comportamento**. Serve para ver, com uso real, onde fica o limiar certo. |
| `enforce` | Só responde a voz cadastrada. As outras são **ignoradas**. |

Em `enforce`, uma voz que não é reconhecida:

- **não abre turno** e não recebe resposta, nem um «quem é você?»; o que ela disse não chega ao modelo nem ao log;
- **não corta** o que a Juniper está dizendo, não confirma nem nega uma ação pendente e não dá comando falado («para», «encerrar»);
- vira o estado `unrecognized` na tela (só se a Juniper estiver parada), seguido de `listening`; o evento `speaker_ignored` registra a semelhança.

Os detalhes que importam:

- **Sem ninguém cadastrado**, `enforce` se comporta como `off`: não há com quem comparar, e travar tudo seria trancar o dono para fora.
- **Se o modelo quebra ou nunca carregou, ela não responde a ninguém** (e o log diz o porquê, `speaker_error`): uma instalação defeituosa não vira «responde a todos».
- **Falar por cima dela não a interrompe** enquanto só vozes cadastradas valem, porque 300 ms de energia não dizem de quem é a voz. Uma voz cadastrada que fala por cima é reconhecida ao fim da frase e então assume a vez; o botão continua cortando na hora.
- **Respostas muito curtas** («sim») vão com o pedaço de silêncio de antes e de depois, que ajuda a atingir o tamanho mínimo do vetor, mas uma voz difícil de reconhecer pode ser ignorada; com um modelo real isso precisa ser medido. Uma confirmação importante pode ser dita numa frase («sim, pode fazer»).
- **Transcrição do aparelho** (`voice.text`, sem áudio no cérebro): não há voz para comparar; a fala vale como a do dono da credencial.
- **Onde o áudio de uma voz desconhecida vai**: por padrão a transcrição e o reconhecimento rodam juntos para ganhar tempo, então o áudio chega ao provedor de transcrição mesmo de quem será ignorado. Com `voice.speaker.identify_first: true` nada disso sai do servidor, ao custo do tempo do vetor.
- **Quem fala chega ao modelo de linguagem** só como nome numa nota do turno («Quem está falando agora: Ana»), para ela saber como chamar a pessoa. A nota diz que reconhecer a voz não autentica nada. O `voice.transcript` leva `speaker` e `speaker_confidence`.
- A saudação de quem abre pela palavra usa o nome da pessoa dona da credencial do aparelho; nenhum nome está no código.

## O modelo e o limiar {#modelo}

O vetor vem de um modelo de reconhecimento de falante em ONNX, rodando na CPU pelo **sherpa-onnx** (biblioteca Apache-2.0; modelos como WeSpeaker ou 3D-Speaker). Ambos são opcionais até alguém usar o reconhecimento:

1. Instale o extra: `pip install ".[voice-id]"`. A imagem de produção ainda **não** o instala.
2. Coloque o arquivo do modelo no servidor e aponte `voice.speaker.model_file` para ele. A licença e o desempenho de cada modelo precisam ser conferidos antes de escolher um.

A lista de vozes é **uma só para o servidor**, não por aparelho: a voz cadastrada de Ana é reconhecida no aparelho de Petrus, e ela usa o cérebro e os dados desse aparelho (a identidade só dá o nome; ver acima). Uma pessoa **desativada** (`juniper users disable`) deixa de ser respondida em até um minuto, mas a voz dela continua cadastrada até `juniper voices forget`.

A decisão «é fulano» exige duas coisas: a semelhança chegar a `voice.speaker.threshold` e a vantagem sobre a segunda pessoa cadastrada chegar a `voice.speaker.margin`. Duas vozes parecidas demais resultam em **ninguém**, nunca em um palpite. O limiar padrão (0,6) é só um ponto de partida: alto demais deixa a Juniper surda para o dono, baixo demais a faz responder a quem não deveria. Calibre com `juniper voices test` e o modo `shadow`.

As opções estão em [Configuração](configuracao.md#voice).

## O que foi verificado {#verificado}

| Como | O quê |
|---|---|
| **Automatizado** (`tests/test_voice_speaker.py`, `tests/test_voices_cli.py`) | Cifra e vínculo ao registro, consentimento, recusa de amostras ruins ou de vozes misturadas, limiar e margem, cache, exclusão, a CLI inteira |
| **Simulado** | Vozes representadas por conjuntos de tons e um extrator de mentira (`tests/fake_speaker.py`); mostra que a lógica de comparação funciona, não que um modelo real separa pessoas |
| **Com modelo e vozes reais** | Pendente: falso aceite, falsa rejeição e o limiar certo só existem com pessoas de verdade |

## Para saber mais {#para-saber-mais}

- [Voz por áudio](voz-audio.md) (Documentação Técnica): a sessão de voz onde o reconhecimento vai atuar.
- [Usuários e credenciais](usuarios.md) (Documentação Técnica): a quem uma voz cadastrada se liga.
- [Avaliar a voz](voz-avaliacao.md) (Documentação Técnica): como medir; os falsos aceites e rejeições entram nas mesmas medições.
- [Privacidade e segurança](produto-privacidade.md) (Produto): para onde vão os seus dados.
- [Configuração](configuracao.md) (Referência): as opções `voice.speaker.*`.
