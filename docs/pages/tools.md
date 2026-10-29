---
title: Tools
group: Documentação Técnica
section: Núcleo
order: 4
description: Como uma tool é definida, registrada e executada: Tool, ToolResult, Tier, Locality e o registry.
status: pronta
tags: ferramenta registry register_tool schema function calling precheck confirm
---

## Visão geral {#visao-geral}

Uma **tool** é uma capacidade que o LLM pode escolher usar ([ADR-002](../decisions/ADR-002.md)) (consultar a agenda, buscar commits, guardar uma memória). Cada tool é uma classe que declara o que faz, que parâmetros recebe e que nível de segurança tem. Para criar uma nova basta **um arquivo novo** em `brain/tools/`: ela se registra sozinha e nada no cérebro muda.

O fluxo: o arquivo da tool tem `@register_tool` na classe → o [registry](#toolregistry) guarda nome → classe → o [Dispatcher](agente.md#dispatcher) pega a classe pelo nome e executa → o LLM só enxerga o que `registry.schemas()` devolve.

## Tool {#tool kind=classe file=brain/tools/base.py}

Classe abstrata (`brain/tools/base.py`). Cada ferramenta herda dela, declara os metadados como atributos de classe e implementa `async run()`.

```python
from brain.tools.base import Locality, Tier, Tool, ToolResult
from brain.tools.registry import register_tool

@register_tool
class OpenApp(Tool):
    name = "open_app"                      # snake_case, único
    description = "Abre um aplicativo ..."  # PT-BR: o que faz e quando usar
    parameters = {                          # JSON Schema
        "type": "object",
        "properties": {"target": {"type": "string", "description": "..."}},
        "required": ["target"],
    }
    tier = Tier.WRITE                       # opcional (padrão WRITE)
    locality = Locality.EXECUTOR            # opcional (padrão EXECUTOR)

    async def run(self, **kwargs) -> ToolResult:
        return ToolResult(success=True, output="abri firefox")
```

| Atributo | O que é | Padrão |
|---|---|---|
| `name` | Nome em `snake_case`, único. | obrigatório |
| `description` | Texto em PT-BR que diz **o que faz e quando usar** (é o que o LLM lê). | obrigatório |
| `parameters` | JSON Schema do tipo `object`. | obrigatório |
| `tier` | Nível de segurança ([Tier](#tier)). | `WRITE` |
| `locality` | Onde roda ([Locality](#locality)). | `EXECUTOR` |
| `confirm` | Se pede confirmação. **Só `WRITE` pode** pôr `False`. | `True` |

**Ganchos opcionais** (métodos que a tool pode sobrescrever):

- `precheck(**args)`: roda **antes** de te perguntar. Pode recusar uma entrada inválida ou em conflito (devolvendo um `ToolResult(success=False)`) sem te incomodar e sem gastar uma volta do LLM.
- `describe(**args)`: resumo de uma linha do que a chamada fará, usado na pergunta de confirmação.
- `subjects(**args)`: os nomes que a pergunta precisa citar para o seu "sim" valer para aquilo (veja [confirmação](agente.md#confirmacao)).

### O que o LLM enxerga: `to_schema()` {#to-schema}

`Tool.to_schema()` converte os metadados para o formato OpenAI function calling do ADR-002, exatamente o que os providers esperam no campo `tools`:

```json
{"type": "function", "function": {"name": "open_app", "description": "...", "parameters": {"type": "object", "...": "..."}}}
```

`tier` e `locality` ficam **de fora**: são assunto do dispatcher, não do modelo. O schema devolvido é uma cópia (alterá-lo não muda a classe).

### Validação na definição {#validacao}

Quando o módulo da tool é importado, `__init_subclass__` confere os metadados; uma tool malformada falha **no import**, não no meio de uma conversa.

| Regra | Erro |
|---|---|
| `name` em `snake_case` (`open_app`, não `OpenApp` nem `open-app`) | `ValueError` |
| `description` não vazia | `ValueError` |
| `parameters` é um objeto JSON Schema: `type: "object"`, `properties` dict, `required` só com chaves existentes | `ValueError` |
| `tier`/`locality` são membros dos enums (uma string solta é rejeitada) | `ValueError` |
| Só `WRITE` pode ter `confirm = False` | `ValueError` |

Classes abstratas intermediárias (sem `run`) não são validadas.

## ToolResult {#toolresult kind=classe file=brain/tools/base.py}

O retorno único de toda tool (ADR-002), imutável: `success`, `output`, `error` e `metadata`. Nenhuma tool levanta exceção para o agente: tudo volta como `ToolResult`, e o LLM explica o problema a você.

## Tier {#tier kind=classe file=brain/tools/base.py}

Enum `StrEnum` com os quatro níveis do [ADR-006](../decisions/ADR-006.md) (valores em minúsculas, para serializar bem no log).

| Tier | Comportamento | Exemplos |
|---|---|---|
| `READ` | Executa direto. | `list_events`, `git_search`, `recall` |
| `WRITE` | Pede o seu "sim" na conversa (a menos que `confirm = False`). | `create_event`, `update_event`, `remember` |
| `DESTRUCTIVE` | Pede o "sim" com aviso de irreversibilidade. Nunca dispensa a confirmação. | `delete_event`, `git_deploy`, `forget` |
| `FORBIDDEN` | Nunca executa, nem com confirmação. | — |

Uma tool sem classificação é tratada como `WRITE`, para forçar uma revisão consciente em vez de executar por esquecimento. Detalhes em [Segurança](seguranca.md).

## Locality {#locality kind=classe file=brain/tools/base.py}

Onde a tool roda ([ADR-007](../decisions/ADR-007.md)): `CLOUD` (no cérebro: agenda, memória, música) ou `EXECUTOR` (no dispositivo do usuário: git local, abrir uma URL). No modo local tudo roda no mesmo processo; no modo servidor, as de `EXECUTOR` são enviadas ao dispositivo conectado que as registrou ([Executor](executor.md), [ADR-014](../decisions/ADR-014.md)).

## ToolRegistry {#toolregistry kind=classe file=brain/tools/registry.py}

A lista de tools, que se monta sozinha. Guarda **classes**, não instâncias (o dispatcher cria a instância na hora de executar).

```python
from brain.tools.registry import registry

registry.discover()               # na partida
registry.get("list_events")       # a classe, ou None
registry.all()                    # todas as classes
registry.schemas()                # para o campo `tools` do LLM
```

### register_tool {#register-tool kind=função file=brain/tools/registry.py}

Decorator que registra a classe. Há um objeto `registry` global e o decorator aponta para ele; os testes criam `ToolRegistry()` separados.

| Situação | Resultado |
|---|---|
| Nome já registrado por outra classe | `ValueError` |
| Não é uma subclasse concreta de `Tool` | `TypeError` |
| Registrar de novo a mesma classe (recarregar módulo) | não é erro |
| `get()` de nome desconhecido | `None` (o dispatcher transforma em `ToolResult(success=False)` para o LLM) |

### Auto-descoberta {#descoberta}

`registry.discover()` importa **todos** os módulos de `brain/tools/`; importar um módulo executa o `@register_tool` das classes dele. Ignora `base`, `registry` e módulos que começam com `_`. Um módulo que não importa (dependência faltando) **propaga o erro** na partida: uma tool quebrada que some em silêncio é pior que um erro claro. A descoberta é explícita: se ninguém chamar `discover()`, o registry fica vazio.

## Tools disponíveis {#disponiveis}

`juniper tools` lista as tools registradas com nome, tier, locality e descrição.

| Tool | Tier | Locality | O que faz | Página |
|---|---|---|---|---|
| `list_events` | READ | CLOUD | Lista compromissos de um intervalo. | [Agenda Google](google-calendar.md) |
| `create_event` | WRITE | CLOUD | Marca um compromisso (checa conflitos). | [Agenda Google](google-calendar.md) |
| `update_event` | WRITE | CLOUD | Muda título, horário ou observações. | [Agenda Google](google-calendar.md) |
| `delete_event` | DESTRUCTIVE | CLOUD | Apaga um evento (irreversível). | [Agenda Google](google-calendar.md) |
| `git_search` | READ | EXECUTOR | Busca commits por termo. | [Comandos git](git.md) |
| `git_deploy` | DESTRUCTIVE | EXECUTOR | Cherry-pick de feature em develop/stage. | [Comandos git](git.md) |
| `play_music` | WRITE (sem perguntar) | CLOUD | **Toca** uma música: busca, escolhe a melhor e, se o pedido veio do app do celular, abre e dá o play; de outros canais devolve o link para a resposta. O pedido já é a confirmação. | [Música](musica.md#play-music) |
| `music_search` | READ | CLOUD | Procura músicas no YouTube Music e devolve links (só aparece com `music.enabled` e a chave configurada). | [Música](musica.md) |
| `music_playlists` | READ | CLOUD | Lista as playlists do YouTube Music (precisa do login). | [Música](musica.md#playlists) |
| `music_playlist_create` | WRITE | CLOUD | Cria uma playlist privada com as músicas dadas (até 25). | [Música](musica.md#playlists) |
| `music_playlist_add` | WRITE | CLOUD | Adiciona músicas a uma playlist existente (até 25 por vez). | [Música](musica.md#playlists) |
| `music_feedback` | WRITE (sem confirmar) | CLOUD | Anota o gosto musical a partir do que o usuário pede e comenta (precisa de MongoDB). | [Música](musica.md#gosto) |
| `open_url` | WRITE (sem perguntar em domínio de confiança) | EXECUTOR | Abre um link no dispositivo (no celular, o app que trata o endereço, como o YouTube Music). | [Executor](executor.md#open-url) |
| `remember` | WRITE (sem confirmar) | CLOUD | Guarda um fato na memória de longo prazo. | [Memória de longo prazo](memoria-longo-prazo.md#tools-de-memoria) |
| `update_profile` | WRITE (sem confirmar) | CLOUD | Atualiza o perfil do usuário. | [Memória de longo prazo](memoria-longo-prazo.md#tools-de-memoria) |
| `recall` | READ | CLOUD | Busca na memória por significado. | [Memória de longo prazo](memoria-longo-prazo.md#tools-de-memoria) |
| `forget` | DESTRUCTIVE | CLOUD | Apaga memórias. | [Memória de longo prazo](memoria-longo-prazo.md#tools-de-memoria) |
| `docs_search` | READ | CLOUD | Busca na própria documentação, por palavras-chave, para responder sobre si mesma. | [Metadocumentação](metadocs.md#docs-search) |

Ainda não implementadas: `run_shell` e `open_app` (arquivo `system.py`), `web_fetch` e `web_search` (arquivo `web.py`). As tools de memória só aparecem para o LLM quando a [memória de longo prazo](memoria.md) está ligada.

## Limitações conhecidas {#limitacoes}

- A validação do schema é rasa: confere a estrutura mínima, não se cada propriedade é um JSON Schema válido.
- O tier é fixo por classe. A futura `run_shell` terá tier dinâmico (decidido pelo comando), e o tier da classe servirá de piso.
- A **confirmação**, ao contrário, pode depender da chamada: uma tool `WRITE` sobrescreve `requires_confirmation(**args)` (padrão: segue `confirm`) para dispensar a pergunta em chamadas seguras pelos próprios argumentos, como `open_url` com um domínio de confiança. `DESTRUCTIVE` sempre pergunta, e um gancho que falha também pergunta.
- O registry é global do processo; os testes isolam isso trocando o conteúdo dele. Não há `unregister`.
- A ordem de `all()` e `schemas()` não é estável entre arquivos.

## Testes {#testes}

```bash
pytest tests/test_tools_base.py tests/test_tools_registry.py -v
```

Cobrem: classe não instanciável, `to_schema()` igual ao formato do ADR-002 (e cópia independente), tier padrão `WRITE`, cada regra de validação, registrar e consultar, nome duplicado, registro idempotente e a auto-descoberta com pacotes temporários (arquivo novo aparece sem lista central).

## Para saber mais {#para-saber-mais}

- [Ferramentas e permissões](produto-ferramentas.md) (Produto): Como a Juniper age no mundo real por meio de ferramentas, os quatro níveis de risco e como a permissão é dada na própria conversa.
- [Glossário](glossario.md) (Produto): Os termos usados nesta documentação, explicados em uma ou duas frases.
- [Visão geral da arquitetura](architecture.md) (Documentação Técnica): As camadas da Juniper, onde cada uma mora no código, as regras que as mantêm simples, os modos de execução e o caminho de uma mensagem.
- [Agente](agente.md) (Documentação Técnica): O loop que decide, chama tools e reavalia: Agent, Dispatcher, Session e a confirmação na conversa.
- [Segurança](seguranca.md) (Documentação Técnica): Os quatro tiers, a confirmação na conversa, o tratamento de segredos e a regra "conteúdo externo é dado".
- [Metadocumentação](metadocs.md) (Documentação Técnica): Como a Juniper responde sobre a própria arquitetura: detector de perguntas por palavras-chave, busca BM25 local na documentação, contexto automático e a tool docs_search.
- [Executor](executor.md) (Documentação Técnica): O dispositivo como corpo da Juniper: comandos estruturados, revalidação local e a tool open_url.
- [Agenda Google](google-calendar.md) (Documentação Técnica): Ler, marcar, alterar e desmarcar compromissos no Google Calendar, com checagem de conflitos.
- [Música](musica.md) (Documentação Técnica): Achar e tocar músicas pelo YouTube Music do celular, criar playlists e aprender o gosto musical, pela YouTube Data API oficial.
- [Comandos git](git.md) (Documentação Técnica): search e deploy no terminal e pelo agente (git_search e git_deploy).
