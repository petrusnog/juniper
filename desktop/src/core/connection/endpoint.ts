/**
 * Which brain addresses the app agrees to connect to (ADR-033 section 2, ADR-013).
 *
 * `ws://` (no TLS) is only for the private network: a Tailscale address (100.64.0.0/10) or a
 * `*.ts.net` name. Anything else needs `wss://`, and certificate validation is never switched off.
 */
export interface EndpointCheck {
  ok: boolean;
  url?: string;
  reason?: string;
}

function inTailscaleRange(host: string): boolean {
  const m = /^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$/.exec(host);
  if (m === null) {
    return false;
  }
  const [a, b] = [Number(m[1]), Number(m[2])];
  const octets = [m[1], m[2], m[3], m[4]].map(Number);
  if (octets.some(o => o > 255)) {
    return false;
  }
  return a === 100 && b >= 64 && b <= 127;
}

export const DEFAULT_PORT = '8765';
export const WS_PATH = '/v1/ws';

/**
 * Completes what a person naturally types: `100.69.153.120`, `100.69.153.120:8765` or
 * `ws://100.69.153.120` become `ws://100.69.153.120:8765/v1/ws`. Without the port, `ws://` goes to
 * port 80, and without the path the brain answers 404: in both cases nothing reaches it.
 */
export function completeEndpoint(raw: string): string {
  const text = raw.trim();
  const hasScheme = /^[a-z][a-z0-9+.-]*:\/\//i.test(text);
  const host = (text.split(/[:/]/)[0] ?? '').toLowerCase();
  // No scheme typed: a Tailscale address is the normal case (ws://), anything else needs TLS.
  const scheme = inTailscaleRange(host) || host.endsWith('.ts.net') ? 'ws' : 'wss';
  const withScheme = hasScheme ? text : `${scheme}://${text}`;
  let url: URL;
  try {
    url = new URL(withScheme);
  } catch {
    return text; // let the validation say it is invalid
  }
  if (url.protocol === 'ws:' && url.port === '') url.port = DEFAULT_PORT;
  if (url.pathname === '' || url.pathname === '/') url.pathname = WS_PATH;
  return url.toString();
}

export function checkEndpoint(input: string): EndpointCheck {
  const raw = completeEndpoint(input);
  let url: URL;
  try {
    url = new URL(raw);
  } catch {
    return { ok: false, reason: 'endereço inválido' };
  }
  if (url.username !== '' || url.password !== '') {
    return { ok: false, reason: 'o endereço não pode levar usuário e senha' };
  }
  const host = url.hostname.toLowerCase();
  if (url.protocol === 'wss:') {
    return { ok: true, url: url.toString() };
  }
  if (url.protocol === 'ws:') {
    if (inTailscaleRange(host) || host.endsWith('.ts.net')) {
      return { ok: true, url: url.toString() };
    }
    return {
      ok: false,
      reason: 'ws:// só vale na rede privada (Tailscale); fora dela use wss://',
    };
  }
  return { ok: false, reason: 'use ws:// (Tailscale) ou wss://' };
}
