---
title: Reconhecer quem fala
group: Documentação Técnica
section: Clientes
order: 29
description: Como a Juniper aprende a voz de quem se cadastrou, onde guarda esse dado biométrico e como o protege, como cadastrar, testar e apagar uma voz, e o que a voz reconhecida pode e não pode decidir.
status: proposto
tags: voz identidade speaker biometria voiceprint embedding cadastro consentimento sherpa-onnx cifra aes-gcm limiar privacidade adr-034
---

> **Nota:** esta página detalha a seção 3 do [ADR-034](../decisions/ADR-034.md). Código: `brain/voice/speaker.py` e `brain/clients/voices_cli.py`. **Estado:** parcial — o cadastro, o cofre e a comparação estão prontos e testados com um extrator de mentira; falta ligar o reconhecimento à sessão de voz (T-214) e **nunca rodou com um modelo de voz de verdade**.

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
- **Preso ao próprio registro.** A cifra amarra o vetor ao id do cadastro e ao usuário: mover o registro para outra pessoa o torna ilegível, em vez de passar a valer para ela.
- **Só com consentimento.** O cadastro exige o «sim» da pessoa (`--consent` ou a pergunta na tela) e guarda quem registrou e quando.
- **Sem áudio guardado.** As gravações são lidas, viram vetor e são descartadas.
- **Apagar é imediato e completo**, por voz ou por pessoa.
- **Nunca em log, cliente ou terceiro.** Só a semelhança (um número) pode ir ao log. A lista (`juniper voices list`) não mostra o vetor.
- **Perdeu a chave?** Os cadastros ficam ilegíveis (são ignorados, não dão erro) e é preciso cadastrar de novo. Guarde um backup da chave **à parte** do backup do banco: o `backup_memory.sh` leva o banco, não a chave.
- **Trocou de modelo?** Cada cadastro lembra qual modelo o gerou e é ignorado (nunca misturado) se o modelo configurado for outro.

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

## O modelo e o limiar {#modelo}

O vetor vem de um modelo de reconhecimento de falante em ONNX, rodando na CPU pelo **sherpa-onnx** (biblioteca Apache-2.0; modelos como WeSpeaker ou 3D-Speaker). Ambos são opcionais até alguém usar o reconhecimento:

1. Instale o extra: `pip install ".[voice-id]"`. A imagem de produção ainda **não** o instala.
2. Coloque o arquivo do modelo no servidor e aponte `voice.speaker.model_file` para ele. A licença e o desempenho de cada modelo precisam ser conferidos antes de escolher um.

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
