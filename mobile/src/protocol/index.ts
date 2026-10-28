/** The wire protocol (ADR-012) as the app sees it: typed messages, parsing and serializing. */
import type { JuniperClientBrainProtocol as AnyMessage } from './types.generated';
import { MAX_MESSAGE_BYTES, PROTOCOL_VERSION } from './types.generated';

export {
  ERROR_CODES,
  MAX_MESSAGE_BYTES,
  PROTOCOL_VERSION,
} from './types.generated';
export type { AnyMessage };

/** The message whose `type` is `T` (the generated types leave `type` optional: it has a default). */
export type Msg<T extends string> = Extract<AnyMessage, { type?: T }> & {
  type: T;
};

export class ProtocolError extends Error {}

/** Parses one text frame from the brain. Throws `ProtocolError` for anything malformed. */
export function parseMessage(frame: string): AnyMessage & { type: string } {
  if (frame.length > MAX_MESSAGE_BYTES) {
    throw new ProtocolError('frame too large');
  }
  let data: unknown;
  try {
    data = JSON.parse(frame);
  } catch {
    throw new ProtocolError('invalid JSON');
  }
  if (
    typeof data !== 'object' ||
    data === null ||
    typeof (data as { type?: unknown }).type !== 'string'
  ) {
    throw new ProtocolError('message without a type');
  }
  return data as AnyMessage & { type: string };
}

export function isType<T extends string>(
  message: { type?: string },
  type: T,
): message is Msg<T> {
  return message.type === type;
}

export function serialize(message: AnyMessage & { type: string }): string {
  const text = JSON.stringify(message);
  if (text.length > MAX_MESSAGE_BYTES) {
    throw new ProtocolError('message too large');
  }
  return text;
}

export const CLOSE_UNAUTHORIZED = 4401;
export const CLOSE_UNSUPPORTED_VERSION = 4426;
export const CLOSE_REPLACED = 4409;
export const CLOSE_SERVICE_RESTART = 1012;

export { PROTOCOL_VERSION as VERSION };
