/**
 * The desktop as an executor (ADR-014, ADR-033).
 *
 * On every connection it registers its tools; for each `invoke` it revalidates, in this order:
 * the tool is in the catalogue it declared (`unknown_tool`), the arguments obey its schema
 * (`invalid_args`), the tier that came is the declared one (`tier_mismatch`), and the tool's own
 * policy accepts them (`policy_denied`, inside the handler). Every refusal is a typed result,
 * never an exception. A `call_id` already answered is not run again for ten minutes (a repeated
 * `invoke` after a reconnection must not trash a file twice).
 */
import { isType } from '../../shared/protocol';
import type { Msg } from '../../shared/protocol';
import type { BrainConnection } from '../connection/BrainConnection';
import { validateArguments } from './schema';

export type Tier = 'read' | 'write' | 'destructive';

export interface ToolOutcome {
  success: boolean;
  output?: string;
  error?: string;
  metadata?: Record<string, unknown>;
}

export interface LocalTool {
  name: string;
  description: string;
  parameters: Record<string, unknown>;
  tier: Tier;
  handle(args: Record<string, unknown>): Promise<ToolOutcome>;
}

export interface ActivityEntry {
  at: number;
  tool: string;
  outcome: 'ok' | 'failed' | 'refused';
  code?: string;
  detail?: string;
}

const REPLAY_WINDOW_MS = 10 * 60 * 1000;
const ACTIVITY_LIMIT = 100;

export class Executor {
  readonly activity: ActivityEntry[] = [];
  private readonly answered = new Map<string, { at: number; outcome: Promise<ToolOutcome> }>();
  private readonly paused = new Set<string>();
  private off: (() => void) | null = null;

  constructor(
    private readonly connection: BrainConnection,
    private readonly tools: LocalTool[],
    private readonly now: () => number = Date.now,
    /** Called with every entry (the main process writes the local log, without content). */
    private readonly onActivity: (entry: ActivityEntry) => void = () => undefined,
  ) {}

  start(): void {
    this.off = this.connection.onMessage(message => {
      if (isType(message, 'welcome')) {
        this.register();
      } else if (isType(message, 'invoke')) {
        this.handle(message).catch(() => undefined);
      }
    });
  }

  stop(): void {
    this.off?.();
    this.off = null;
  }

  /** The kill switch for a group of tools (ADR-033): the names starting with `prefix` go away. */
  setPaused(prefix: string, paused: boolean): void {
    if (paused) this.paused.add(prefix);
    else this.paused.delete(prefix);
    this.register();
  }

  isPaused(prefix: string): boolean {
    return this.paused.has(prefix);
  }

  private offered(): LocalTool[] {
    return this.tools.filter(t => ![...this.paused].some(prefix => t.name.startsWith(prefix)));
  }

  private register(): void {
    this.connection.send({
      type: 'register',
      tools: this.offered().map(t => ({
        name: t.name,
        description: t.description,
        parameters: t.parameters,
        tier: t.tier,
      })),
    });
  }

  private record(entry: Omit<ActivityEntry, 'at'>): void {
    const full = { at: this.now(), ...entry };
    this.activity.push(full);
    if (this.activity.length > ACTIVITY_LIMIT) this.activity.shift();
    this.onActivity(full);
  }

  private reply(callId: string, outcome: ToolOutcome): void {
    this.connection.send({ type: 'result', call_id: callId, ...outcome });
  }

  private refuse(invoke: Msg<'invoke'>, code: string, error: string): void {
    this.record({ tool: invoke.tool_name, outcome: 'refused', code });
    this.reply(invoke.call_id, { success: false, error, metadata: { code } });
  }

  private async handle(invoke: Msg<'invoke'>): Promise<void> {
    const now = this.now();
    for (const [id, entry] of this.answered) {
      if (now - entry.at > REPLAY_WINDOW_MS) this.answered.delete(id);
    }
    const seen = this.answered.get(invoke.call_id);
    if (seen !== undefined) {
      this.reply(invoke.call_id, await seen.outcome); // same answer, never the action twice
      return;
    }
    const tool = this.offered().find(t => t.name === invoke.tool_name);
    if (tool === undefined) {
      return this.refuse(invoke, 'unknown_tool', `Este computador não executa ${invoke.tool_name}.`);
    }
    const problem = validateArguments(tool.parameters, invoke.arguments ?? {});
    if (problem !== null) {
      return this.refuse(invoke, 'invalid_args', `Argumentos inválidos: ${problem}.`);
    }
    if (invoke.tier !== tool.tier) { // a missing tier is a mismatch too (the brain always sends it)
      return this.refuse(invoke, 'tier_mismatch', 'O tipo de ação recebido não confere com o declarado.');
    }
    // The answer is remembered BEFORE running, so two invokes with the same call_id in flight at
    // once still run the action only once.
    const running = tool.handle(invoke.arguments ?? {}).catch(
      (): ToolOutcome => ({
        success: false,
        error: 'Não consegui executar a ação neste computador.',
        metadata: { code: 'os_error' },
      }),
    );
    this.answered.set(invoke.call_id, { at: this.now(), outcome: running });
    const outcome = await running;
    const code = typeof outcome.metadata?.code === 'string' ? outcome.metadata.code : undefined;
    this.record({
      tool: tool.name,
      outcome: outcome.success ? 'ok' : code === 'policy_denied' ? 'refused' : 'failed',
      ...(code ? { code } : {}),
    });
    this.reply(invoke.call_id, outcome);
  }
}
