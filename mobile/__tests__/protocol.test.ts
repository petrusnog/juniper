import { execFileSync } from 'child_process';
import path from 'path';
import {
  isType,
  MAX_MESSAGE_BYTES,
  parseMessage,
  ProtocolError,
  PROTOCOL_VERSION,
  serialize,
} from '../src/protocol';

test('the generated protocol types are up to date with docs/protocol/schema.json', () => {
  // A child process: the generator is ESM-flavoured and does not run inside Jest's sandbox.
  const script = path.join(__dirname, '..', 'scripts', 'gen-protocol-types.js');
  expect(() =>
    execFileSync(process.execPath, [script, '--check'], { stdio: 'pipe' }),
  ).not.toThrow();
});

test('parses a server frame and narrows it by type', () => {
  const message = parseMessage(
    '{"type":"chat.token","turn_id":"t-1","text":"oi"}',
  );
  expect(isType(message, 'chat.token')).toBe(true);
  if (isType(message, 'chat.token')) {
    expect(message.text).toBe('oi');
  }
});

test.each([['not json'], ['[]'], ['null'], ['{"no":"type"}'], ['{"type":3}']])(
  'rejects the malformed frame %s',
  frame => {
    expect(() => parseMessage(frame)).toThrow(ProtocolError);
  },
);

test('rejects a frame above the protocol limit', () => {
  const huge = JSON.stringify({
    type: 'x',
    pad: 'a'.repeat(MAX_MESSAGE_BYTES),
  });
  expect(() => parseMessage(huge)).toThrow(ProtocolError);
});

test('serializes a message and refuses one that is too large', () => {
  expect(serialize({ type: 'pong' })).toBe('{"type":"pong"}');
  expect(() =>
    serialize({
      type: 'chat.send',
      id: '1',
      text: 'a'.repeat(MAX_MESSAGE_BYTES + 1),
    }),
  ).toThrow(ProtocolError);
});

test('speaks the same protocol version as the brain', () => {
  expect(PROTOCOL_VERSION).toBe(1);
});
