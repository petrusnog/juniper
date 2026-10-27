/* eslint-disable no-bitwise -- bit arithmetic on addresses */
/**
 * URL policy for `open_url` (ADR-014, section 6): what this device agrees to open.
 *
 * A port of `brain/tools/url_policy.py`. The brain checks first, but "the client revalidates
 * everything": a compromised or confused brain must not be able to make the phone open
 * `file://`, an address on the local network or a URL that a browser reads differently from a
 * parser. The cases in `tests/fixtures/url_policy_cases.json` run against both implementations
 * (pytest and Jest) so they cannot drift. The policy never resolves DNS.
 */
import punycode from 'punycode';

export const MAX_URL_CHARS = 2048;
const MAX_HOST_CHARS = 253;
const ALLOWED_SCHEMES = new Set(['http', 'https']);
const NUMERIC_HOST = /^(0x[0-9a-f]+|[0-9]+)(\.(0x[0-9a-f]+|[0-9]+)){0,3}$/i;
// eslint-disable-next-line no-control-regex
const ASCII_ONLY = /^[\x00-\x7f]*$/;
const LABEL = /^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$/;
const PATH_SAFE = "/%:@!$&'()*+,;=-._~";
const QUERY_SAFE = "/%:@!$&'()*+,;=-._~?";

export interface UrlCheck {
  ok: boolean;
  url?: string;
  host?: string;
  /** PT-BR, safe to show to the user and to hand back to the brain. */
  reason?: string;
}

const deny = (reason: string): UrlCheck => ({ ok: false, reason });
const LOCAL_DENIED = 'endereços locais ou de rede privada não são permitidos';

// --- addresses -----------------------------------------------------------------------------

type Bytes = number[];

function parseDecimalV4(host: string): Bytes | null {
  const parts = host.split('.');
  if (parts.length !== 4) {
    return null;
  }
  const out: number[] = [];
  for (const part of parts) {
    if (!/^(0|[1-9][0-9]{0,2})$/.test(part) || Number(part) > 255) {
      return null;
    }
    out.push(Number(part));
  }
  return out;
}

/** What `inet_aton` makes of "127.1", "2130706433", "0x7f.1", "0177.0.0.1"; null if invalid. */
function parseLooseV4(host: string): Bytes | null {
  const parts = host.split('.');
  if (parts.length < 1 || parts.length > 4) {
    return null;
  }
  const numbers: number[] = [];
  for (const part of parts) {
    let n: number;
    if (/^0x[0-9a-f]+$/i.test(part)) {
      n = parseInt(part.slice(2), 16);
    } else if (/^0[0-7]*$/.test(part)) {
      n = part.length === 1 ? 0 : parseInt(part, 8);
    } else if (/^[1-9][0-9]*$/.test(part)) {
      n = parseInt(part, 10);
    } else {
      return null;
    }
    if (!Number.isFinite(n)) {
      return null;
    }
    numbers.push(n);
  }
  const last = numbers[numbers.length - 1];
  const head = numbers.slice(0, -1);
  if (head.some(n => n > 255) || last >= 256 ** (4 - head.length)) {
    return null;
  }
  const out = [...head];
  for (let i = 4 - head.length - 1; i >= 0; i--) {
    out.push(Math.floor(last / 256 ** i) % 256);
  }
  return out;
}

function parseV6(host: string): Bytes | null {
  if (!/^[0-9a-f:.]+$/i.test(host) || host.includes(':::')) {
    return null;
  }
  let text = host;
  let tail: Bytes = [];
  const lastColon = text.lastIndexOf(':');
  if (text.includes('.')) {
    const v4 = parseDecimalV4(text.slice(lastColon + 1));
    if (v4 === null) {
      return null;
    }
    tail = v4;
    text = text.slice(0, lastColon + 1) + '0:0';
  }
  const halves = text.split('::');
  if (halves.length > 2) {
    return null;
  }
  const toGroups = (s: string): number[] | null => {
    if (s === '') {
      return [];
    }
    const groups: number[] = [];
    for (const g of s.split(':')) {
      if (!/^[0-9a-f]{1,4}$/i.test(g)) {
        return null;
      }
      groups.push(parseInt(g, 16));
    }
    return groups;
  };
  const left = toGroups(halves[0]);
  const right = halves.length === 2 ? toGroups(halves[1]) : [];
  if (left === null || right === null) {
    return null;
  }
  let groups: number[];
  if (halves.length === 2) {
    const missing = 8 - left.length - right.length;
    if (missing < 1) {
      return null;
    }
    groups = [...left, ...new Array<number>(missing).fill(0), ...right];
  } else {
    groups = left;
  }
  if (groups.length !== 8) {
    return null;
  }
  const out: Bytes = [];
  for (const g of groups) {
    out.push(g >> 8, g & 0xff);
  }
  if (tail.length === 4) {
    out.splice(12, 4, ...tail);
  }
  return out;
}

interface Address {
  v6: boolean;
  bytes: Bytes;
}

function address(host: string): Address | null {
  if (host.includes(':')) {
    const bytes = parseV6(host);
    return bytes ? { v6: true, bytes } : null;
  }
  const strict = parseDecimalV4(host);
  if (strict) {
    return { v6: false, bytes: strict };
  }
  if (NUMERIC_HOST.test(host)) {
    const loose = parseLooseV4(host);
    return loose ? { v6: false, bytes: loose } : null;
  }
  return null;
}

function inNet(bytes: Bytes, net: Bytes, bits: number): boolean {
  for (let i = 0; i < bits; i++) {
    const byte = i >> 3;
    const mask = 0x80 >> (i & 7);
    if ((bytes[byte] & mask) !== (net[byte] & mask)) {
      return false;
    }
  }
  return true;
}

const v4 = (s: string, bits: number): [Bytes, number] => [
  s.split('.').map(Number),
  bits,
];
const V4_PRIVATE: [Bytes, number][] = [
  v4('0.0.0.0', 8),
  v4('10.0.0.0', 8),
  v4('100.64.0.0', 10), // Tailscale and other Shared Address Space (RFC 6598)
  v4('127.0.0.0', 8),
  v4('169.254.0.0', 16),
  v4('172.16.0.0', 12),
  v4('192.0.0.0', 29),
  v4('192.0.0.170', 31),
  v4('192.0.2.0', 24),
  v4('192.168.0.0', 16),
  v4('198.18.0.0', 15),
  v4('198.51.100.0', 24),
  v4('203.0.113.0', 24),
  v4('240.0.0.0', 4),
  v4('255.255.255.255', 32),
];

function v6net(text: string, bits: number): [Bytes, number] {
  return [parseV6(text) as Bytes, bits];
}
const V6_PRIVATE: [Bytes, number][] = [
  v6net('::1', 128),
  v6net('::', 128),
  v6net('::ffff:0:0', 96),
  v6net('64:ff9b:1::', 48),
  v6net('100::', 64),
  v6net('2001::', 23),
  v6net('2001:db8::', 32),
  v6net('2002::', 16),
  v6net('fc00::', 7),
  v6net('fe80::', 10),
];
const V6_EXCEPTIONS: [Bytes, number][] = [
  v6net('2001:1::1', 128),
  v6net('2001:1::2', 128),
  v6net('2001:3::', 32),
  v6net('2001:4:112::', 48),
  v6net('2001:20::', 28),
  v6net('2001:30::', 28),
];
const NAT64 = v6net('64:ff9b::', 96);

function embeddedV4(bytes: Bytes): Bytes | null {
  const head = bytes.slice(0, 12);
  const mapped = [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0xff, 0xff];
  const siit = [0, 0, 0, 0, 0, 0, 0, 0, 0xff, 0xff, 0, 0];
  const zero = new Array<number>(12).fill(0);
  const same = (a: Bytes, b: Bytes) => a.every((x, i) => x === b[i]);
  if (
    same(head, mapped) ||
    same(head, siit) ||
    same(head, zero) ||
    inNet(bytes, NAT64[0], 96)
  ) {
    return bytes.slice(12);
  }
  return null;
}

function blocked(addr: Address): boolean {
  const { bytes } = addr;
  if (addr.v6) {
    const inner = embeddedV4(bytes);
    if (inner !== null && blocked({ v6: false, bytes: inner })) {
      return true;
    }
    if (bytes[0] === 0xff) {
      return true; // multicast
    }
    const isPrivate =
      V6_PRIVATE.some(([net, bits]) => inNet(bytes, net, bits)) &&
      !V6_EXCEPTIONS.some(([net, bits]) => inNet(bytes, net, bits));
    return isPrivate;
  }
  if (bytes[0] >= 224 && bytes[0] <= 239) {
    return true; // multicast
  }
  return V4_PRIVATE.some(([net, bits]) => inNet(bytes, net, bits));
}

// --- names ---------------------------------------------------------------------------------

function validName(host: string): boolean {
  return (
    host.length > 0 &&
    host.length <= MAX_HOST_CHARS &&
    host.split('.').every(l => LABEL.test(l))
  );
}

/** Python's `str.encode('idna')` for the cases that matter: NFKC + lowercase + punycode. */
function toAscii(host: string): string | null {
  const labels = host.replace(/[。．｡]/g, '.').split('.');
  const out: string[] = [];
  for (const raw of labels) {
    if (ASCII_ONLY.test(raw)) {
      out.push(raw);
      continue;
    }
    const mapped = raw.normalize('NFKC').toLowerCase().replace(/ß/g, 'ss');
    try {
      out.push(
        ASCII_ONLY.test(mapped) ? mapped : `xn--${punycode.encode(mapped)}`,
      );
    } catch {
      return null;
    }
  }
  return out.join('.');
}

function quote(text: string, safe: string): string {
  let out = '';
  for (const ch of text) {
    if (/[A-Za-z0-9_.\-~]/.test(ch) || safe.includes(ch)) {
      out += ch;
    } else {
      for (const byte of new TextEncoder().encode(ch)) {
        out += `%${byte.toString(16).toUpperCase().padStart(2, '0')}`;
      }
    }
  }
  return out;
}

// --- parsing (what Python's urlsplit does) ---------------------------------------------------

interface Parts {
  scheme: string;
  netloc: string;
  path: string;
  query: string;
  fragment: string;
}

function split(text: string): Parts | null {
  let rest = text;
  let scheme = '';
  const m = /^([A-Za-z][A-Za-z0-9+.-]*):/.exec(rest);
  if (m) {
    scheme = m[1].toLowerCase();
    rest = rest.slice(m[0].length);
  }
  let netloc = '';
  if (rest.startsWith('//')) {
    const end = rest.slice(2).search(/[/?#]/);
    netloc = end === -1 ? rest.slice(2) : rest.slice(2, 2 + end);
    rest = end === -1 ? '' : rest.slice(2 + end);
    if (
      (netloc.includes('[') && !netloc.includes(']')) ||
      (netloc.includes(']') && !netloc.includes('['))
    ) {
      return null;
    }
  }
  let fragment = '';
  let query = '';
  const hash = rest.indexOf('#');
  if (hash !== -1) {
    fragment = rest.slice(hash + 1);
    rest = rest.slice(0, hash);
  }
  const q = rest.indexOf('?');
  if (q !== -1) {
    query = rest.slice(q + 1);
    rest = rest.slice(0, q);
  }
  return { scheme, netloc, path: rest, query, fragment };
}

/** Host and port out of a netloc, like `hostname` and `port`; null when the port is invalid. */
function hostAndPort(
  netloc: string,
): { host: string; port: number | null } | null {
  const afterAt = netloc.slice(netloc.lastIndexOf('@') + 1);
  let host: string;
  let portText = '';
  if (afterAt.startsWith('[')) {
    const close = afterAt.indexOf(']');
    host = afterAt.slice(1, close);
    const after = afterAt.slice(close + 1);
    if (after.startsWith(':')) {
      portText = after.slice(1);
    } else if (after !== '') {
      return null;
    }
  } else {
    const colon = afterAt.indexOf(':');
    host = colon === -1 ? afterAt : afterAt.slice(0, colon);
    portText = colon === -1 ? '' : afterAt.slice(colon + 1);
  }
  let port: number | null = null;
  if (portText !== '') {
    if (!/^[0-9]+$/.test(portText) || Number(portText) > 65535) {
      return null;
    }
    port = Number(portText);
  }
  return { host: host.toLowerCase(), port };
}

/** Applies the ADR-014 URL policy. Never throws. */
export function checkUrl(url: unknown): UrlCheck {
  if (typeof url !== 'string' || url.trim() === '') {
    return deny('o endereço está vazio');
  }
  const text = url.trim();
  if (text.length > MAX_URL_CHARS) {
    return deny(`o endereço passa de ${MAX_URL_CHARS} caracteres`);
  }
  if (/[\p{C}\s]/u.test(text)) {
    return deny('o endereço tem espaços ou caracteres de controle');
  }
  if (text.includes('\\')) {
    return deny('o endereço está mal formado'); // a browser reads "\" as "/"; a parser does not
  }
  const parts = split(text);
  if (parts === null) {
    return deny('o endereço está mal formado');
  }
  const located = hostAndPort(parts.netloc);
  if (located === null) {
    return deny('o endereço está mal formado');
  }
  if (!ALLOWED_SCHEMES.has(parts.scheme)) {
    return deny('só endereços http e https são permitidos');
  }
  if (parts.netloc === '' || located.host === '') {
    return deny('o endereço não tem um site');
  }
  if (parts.netloc.includes('@')) {
    return deny('endereços com usuário e senha embutidos não são permitidos');
  }
  const trimmed = located.host.replace(/\.+$/, '');
  const ascii = toAscii(trimmed);
  if (ascii === null) {
    return deny('o nome do site é inválido');
  }
  if (ascii === '') {
    return deny('o endereço não tem um site');
  }
  const addr = address(ascii);
  if (addr !== null) {
    if (blocked(addr)) {
      return deny(LOCAL_DENIED);
    }
  } else if (ascii === 'localhost' || ascii.endsWith('.localhost')) {
    return deny(LOCAL_DENIED);
  } else if (NUMERIC_HOST.test(ascii) || !validName(ascii)) {
    return deny('o nome do site é inválido');
  }
  let netloc = ascii.includes(':') ? `[${ascii}]` : ascii;
  if (located.port !== null) {
    netloc = `${netloc}:${located.port}`;
  }
  const query = quote(parts.query, QUERY_SAFE);
  const fragment = quote(parts.fragment, QUERY_SAFE);
  const normalised =
    `${parts.scheme}://${netloc}${quote(parts.path, PATH_SAFE)}` +
    `${query ? `?${query}` : ''}${fragment ? `#${fragment}` : ''}`;
  return { ok: true, url: normalised, host: ascii };
}

export function normaliseDomain(domain: string): string | null {
  const value = domain.trim().toLowerCase().replace(/\.+$/, '');
  if (value === '' || address(value) !== null) {
    return null;
  }
  const ascii = toAscii(value);
  if (
    ascii === null ||
    !ascii.includes('.') ||
    !validName(ascii) ||
    NUMERIC_HOST.test(ascii)
  ) {
    return null;
  }
  return ascii;
}

/** Whether `host` (already checked) is, or is a subdomain of, a trusted domain. */
export function isTrusted(host: string, trustedDomains: string[]): boolean {
  if (address(host) !== null || !validName(host)) {
    return false;
  }
  return trustedDomains.some(domain => {
    const wanted = normaliseDomain(domain);
    return wanted !== null && (host === wanted || host.endsWith(`.${wanted}`));
  });
}
