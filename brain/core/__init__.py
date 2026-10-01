"""Core agnóstico de transporte: agent loop, dispatcher, sessão.

Regra arquitetural: `brain.core` NUNCA importa de `brain.clients`.
A dependência é sempre `clients -> core`. Isso é o que permite novos
clientes (celular, web, voz) sem tocar no core.
"""