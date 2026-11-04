/** `open_url` on the desktop (ADR-014, ADR-033 section 3): opens the link in the default browser. */
import { checkUrl } from '../policy/urlPolicy';
import type { LocalTool, ToolOutcome } from './Executor';

/** Opens a URL in the user's default browser (Electron: `shell.openExternal`). */
export interface Opener {
  open(url: string): Promise<void>;
}

export function openUrlTool(opener: Opener): LocalTool {
  return {
    name: 'open_url',
    description:
      'Abre um link no navegador padrão do computador (por exemplo, uma pesquisa ou um vídeo).',
    parameters: {
      type: 'object',
      properties: {
        url: { type: 'string', description: 'Endereço completo, começando com https://.' },
      },
      required: ['url'],
    },
    tier: 'write',
    async handle(args): Promise<ToolOutcome> {
      const checked = checkUrl(args.url);
      if (!checked.ok || checked.url === undefined) {
        return {
          success: false,
          error: `Não posso abrir esse endereço: ${checked.reason ?? 'endereço recusado'}.`,
          metadata: { code: 'policy_denied' },
        };
      }
      try {
        await opener.open(checked.url); // always the normalised URL, never the raw text
      } catch {
        return {
          success: false,
          error: 'Não consegui abrir o navegador neste computador.',
          metadata: { code: 'os_error', url: checked.url },
        };
      }
      return {
        success: true,
        output: `Abri ${checked.url} no navegador.`,
        metadata: { url: checked.url },
      };
    },
  };
}
