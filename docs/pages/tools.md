---
title: Tools
group: Núcleo
order: 3
description: Como uma tool é definida, registrada e executada: Tool, ToolResult, Tier, Locality e o registry.
status: pronta
tags: ferramenta registry register_tool schema function calling precheck confirm
---

## Visão geral {#visao-geral}

Uma **tool** é uma capacidade que o LLM pode escolher usar (consultar a agenda, buscar commits, guardar uma memória). Cada tool é uma classe que declara o que faz, que parâmetros recebe e que nível de segurança tem. Para criar uma nova basta **um arquivo novo** em `brain/tools/`: ela se registra sozinha e nada no cérebro muda.

O fluxo: o arquivo da tool tem `@register_tool` na classe → o [registry](#toolregistry) guarda nome → classe → o [Dispatcher](agente.html#dispatcher) pega a classe pelo nome e executa → o LLM só enxerga o que `registry.schemas()` devolve.

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
- `subjects(**args)`: os nomes que a pergunta precisa citar para o seu "sim" valer para aquilo (veja [confirmação](agente.html#confirmacao)).

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

Enum `StrEnum` com os quatro níveis do ADR-006 (valores em minúsculas, para serializar bem no log).

| Tier | Comportamento | Exemplos |
|---|---|---|
| `READ` | Executa direto. | `list_events`, `git_search`, `recall` |
| `WRITE` | Pede o seu "sim" na conversa (a menos que `confirm = False`). | `create_event`, `update_event`, `remember` |
| `DESTRUCTIVE` | Pede o "sim" com aviso de irreversibilidade. Nunca dispensa a confirmação. | `delete_event`, `git_deploy`, `forget` |
| `FORBIDDEN` | Nunca executa, nem com confirmação. | — |

Uma tool sem classificação é tratada como `WRITE`, para forçar uma revisão consciente em vez de executar por esquecimento. Detalhes em [Segurança](seguranca.html).

## Locality {#locality kind=classe file=brain/tools/base.py}

Onde a tool roda (ADR-007): `CLOUD` (no cérebro: agenda, memória, web) ou `EXECUTOR` (no dispositivo do usuário: git local, abrir uma URL). Hoje tudo roda no mesmo processo; com o cérebro na VPS, as de `EXECUTOR` passam a ser enviadas ao dispositivo ([Executor](executor.html)).

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
| `list_events` | READ | CLOUD | Lista compromissos de um intervalo. | [Agenda Google](google-calendar.html) |
| `create_event` | WRITE | CLOUD | Marca um compromisso (checa conflitos). | [Agenda Google](google-calendar.html) |
| `update_event` | WRITE | CLOUD | Muda título, horário ou observações. | [Agenda Google](google-calendar.html) |
| `delete_event` | DESTRUCTIVE | CLOUD | Apaga um evento (irreversível). | [Agenda Google](google-calendar.html) |
| `git_search` | READ | EXECUTOR | Busca commits por termo. | [Comandos git](git.html) |
| `git_deploy` | DESTRUCTIVE | EXECUTOR | Cherry-pick de feature em develop/stage. | [Comandos git](git.html) |
| `music_search` | READ | CLOUD | Procura músicas no YouTube Music e devolve links (só aparece com `music.enabled` e a chave configurada). | [Música](musica.html) |
| `music_playlists` | READ | CLOUD | Lista as playlists do YouTube Music (precisa do login). | [Música](musica.html#playlists) |
| `music_playlist_create` | WRITE | CLOUD | Cria uma playlist privada com as músicas dadas (até 25). | [Música](musica.html#playlists) |
| `music_playlist_add` | WRITE | CLOUD | Adiciona músicas a uma playlist existente (até 25 por vez). | [Música](musica.html#playlists) |
| `open_url` | WRITE (sem perguntar em domínio de confiança) | EXECUTOR | Abre um link no dispositivo (no celular, o app que trata o endereço, como o YouTube Music). | [Executor](executor.html#open-url) |
| `remember` | WRITE (sem confirmar) | CLOUD | Guarda um fato na memória de longo prazo. | [Memória de longo prazo](memoria-longo-prazo.html#tools-de-memoria) |
| `update_profile` | WRITE (sem confirmar) | CLOUD | Atualiza o perfil do usuário. | [Memória de longo prazo](memoria-longo-prazo.html#tools-de-memoria) |
| `recall` | READ | CLOUD | Busca na memória por significado. | [Memória de longo prazo](memoria-longo-prazo.html#tools-de-memoria) |
| `forget` | DESTRUCTIVE | CLOUD | Apaga memórias. | [Memória de longo prazo](memoria-longo-prazo.html#tools-de-memoria) |

Ainda não implementadas: `run_shell` e `open_app` (arquivo `system.py`), `web_fetch` e `web_search` (arquivo `web.py`). As tools de memória só aparecem para o LLM quando a [memória de longo prazo](memoria.html) está ligada.

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
