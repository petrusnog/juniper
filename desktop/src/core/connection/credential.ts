/** The device credential of ADR-027 and what may be shown of it. The secret is never logged. */
export interface Credential {
  clientId: string;
  clientSecret: string;
}

export function bearerOf(c: Credential): string {
  return `${c.clientId}.${c.clientSecret}`;
}

const SECRET = /jsk_[A-Za-z0-9_-]{10,}/g;

/** Text safe for a log or an error message: any `jsk_...` secret and Bearer value is masked. */
export function mask(text: string): string {
  return text.replace(SECRET, 'jsk_***').replace(/Bearer\s+[A-Za-z0-9._~+/=-]{8,}/g, 'Bearer ***');
}
