/**
 * The phone as an executor of `open_url` (ADR-007, ADR-014, ADR-022 section 6).
 *
 * On every connection it registers the tool; for each `invoke` it applies the URL policy again
 * (the brain validated too, but "the client revalidates everything") and only then asks the
 * native module to open the link. Failures are typed results, never exceptions, and what was
 * opened or refused is kept in a short in-memory history (no URL is written anywhere else).
 */
import type { BrainConnection } from '../connection/BrainConnection';
import { isType } from '../protocol';
import type { Msg } from '../protocol';
import { MediaAutoplay } from './MediaAutoplay';
import { checkUrl } from './urlPolicy';

export const OPEN_URL_TOOL = {
  name: 'open_url',
  description:
    'Abre um link no celular: o aplicativo que trata o endereço (por exemplo, o YouTube Music ' +
    'para um link de música).',
  parameters: {
    type: 'object',
    properties: {
      url: {
        type: 'string',
        description: 'Endereço completo, começando com https://.',
      },
    },
    required: ['url'],
  },
  tier: 'write',
} as const;

export interface HistoryEntry {
  at: number;
  url: string | null;
  outcome: 'opened' | 'refused' | 'failed';
  detail?: string;
}

const HISTORY_LIMIT = 50;

export class OpenUrlExecutor {
  readonly history: HistoryEntry[] = [];
  private off: (() => void) | null = null;

  constructor(
    private readonly connection: BrainConnection,
    private readonly media: MediaAutoplay,
    private readonly now: () => number = Date.now,
  ) {}

  start(): void {
    this.off = this.connection.onMessage(message => {
      if (isType(message, 'welcome')) {
        this.connection.send({ type: 'register', tools: [OPEN_URL_TOOL] });
      } else if (isType(message, 'invoke')) {
        this.handle(message).catch(() => undefined);
      }
    });
  }

  stop(): void {
    this.off?.();
    this.off = null;
  }

  private record(entry: Omit<HistoryEntry, 'at'>): void {
    this.history.push({ at: this.now(), ...entry });
    if (this.history.length > HISTORY_LIMIT) {
      this.history.shift();
    }
  }

  private reply(
    callId: string,
    result: Omit<Msg<'result'>, 'type' | 'call_id'>,
  ): void {
    this.connection.send({ type: 'result', call_id: callId, ...result });
  }

  private async handle(invoke: Msg<'invoke'>): Promise<void> {
    if (invoke.tool_name !== OPEN_URL_TOOL.name) {
      this.reply(invoke.call_id, {
        success: false,
        error: `Este celular não executa a ferramenta ${invoke.tool_name}.`,
        metadata: { code: 'unknown_tool' },
      });
      return;
    }
    const checked = checkUrl(invoke.arguments?.url);
    if (!checked.ok || checked.url === undefined) {
      this.record({ url: null, outcome: 'refused', detail: checked.reason });
      this.reply(invoke.call_id, {
        success: false,
        error: `Não posso abrir esse endereço: ${
          checked.reason ?? 'endereço recusado'
        }.`,
        metadata: { code: 'policy_denied' },
      });
      return;
    }
    try {
      const opened = await this.media.openAndPlay(checked.url);
      if (!opened.opened) {
        this.record({
          url: checked.url,
          outcome: 'failed',
          detail: opened.detail,
        });
        this.reply(invoke.call_id, {
          success: false,
          error: 'Não consegui abrir o link no celular.',
          metadata: {
            code: 'os_error',
            url: checked.url,
            detail: opened.detail ?? '',
          },
        });
        return;
      }
      if (opened.foreground === false && opened.overlay === false) {
        // In the background without the overlay permission Android drops the request silently:
        // do not claim it opened (the music that is "active" may be the previous one).
        this.record({
          url: checked.url,
          outcome: 'failed',
          detail: 'background_blocked',
        });
        this.reply(invoke.call_id, {
          success: false,
          error:
            'Tentei abrir, mas o aplicativo estava em segundo plano e o Android costuma bloquear ' +
            'isso. Avise o Petrus que ele pode permitir «Exibir sobre outros apps» nas ' +
            'configurações do aplicativo da Juniper, ou abrir o aplicativo.',
          metadata: { code: 'background_blocked', url: checked.url },
        });
        return;
      }
      this.record({
        url: checked.url,
        outcome: 'opened',
        detail: String(opened.autoplay),
      });
      this.reply(invoke.call_id, {
        success: true,
        output: opened.autoplay
          ? `Abri ${checked.url} no celular e a música começou a tocar.`
          : `Abri ${checked.url} no celular, mas não consegui dar play sozinha: a música está ` +
            'pronta e basta tocar no play.',
        metadata: { url: checked.url, autoplay: opened.autoplay === true },
      });
    } catch {
      this.record({ url: checked.url, outcome: 'failed', detail: 'exception' });
      this.reply(invoke.call_id, {
        success: false,
        error: 'Não consegui abrir o link no celular.',
        metadata: { code: 'os_error' },
      });
    }
  }
}
