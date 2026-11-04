# Juniper desktop

Cliente desktop da Juniper (Electron + TypeScript, **só Windows na v1**): chat de texto e
**executor** do computador (`open_url` e as tools de arquivo `fs_*`). É um cliente fino: não tem
lógica de agente nem acesso ao MongoDB. Decisões em [`docs/decisions/ADR-033.md`](../docs/decisions/ADR-033.md);
como funciona em [`docs/pages/desktop.md`](../docs/pages/desktop.md).

## Rodar e testar

```bash
cd desktop
npm install          # o Electron baixa o binário; em CI use ELECTRON_SKIP_BINARY_DOWNLOAD=1
npm test             # vitest (roda em qualquer SO; os casos de NTFS real só no Windows)
npm run lint         # inclui as regras de fronteira do ADR-033 §1.1
npm run typecheck
npm run build        # esbuild -> dist/
npm start            # abre a janela (precisa do Electron instalado)
```

Na primeira execução a janela pede o endereço do cérebro (`ws://100.x.y.z:8765/v1/ws`, dentro do
Tailscale), o `client_id` e o `client_secret` do dispositivo (`juniper users login`, ADR-027).

## Estrutura

```text
src/core/        TypeScript puro (sem Electron nem fs/os/path): conexão, chat, executor, políticas
src/main/        Electron: janela, IPC, cofre (safeStorage), tools de arquivo, platform/ (o SO)
src/preload/     a ponte mínima (contextBridge)
src/renderer/    a janela (texto puro, nunca innerHTML)
src/shared/      tipos do protocolo (gerados de docs/protocol/schema.json) e o contrato do IPC
tests/           vitest
```

`npm run gen:protocol` regenera `src/shared/types.generated.ts`; `npm run check:protocol` falha
se estiver velho.
