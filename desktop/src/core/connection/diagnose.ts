/**
 * Why a connection failed, in words a person can act on. The raw detail comes from the socket
 * (`ECONNREFUSED`, `Unexpected server response: 404`...) or from the close code. Nothing here
 * contains the credential: the detail is masked before it is stored or shown.
 */
import { mask } from './credential';

/** A short, safe description of a socket error or a close. */
export function describeSocketError(event: unknown): string {
  const message =
    typeof event === 'object' && event !== null && 'message' in event
      ? String((event as { message: unknown }).message)
      : '';
  return mask(message).slice(0, 200);
}

export function describeClose(code: number, reason?: string): string {
  const text = reason ? ` (${mask(reason).slice(0, 80)})` : '';
  return `conexão fechada, código ${code}${text}`;
}

/** What to try, for a raw detail. `null` when there is nothing specific to say. */
export function hintFor(detail: string | null): string | null {
  if (detail === null || detail === '') return null;
  const d = detail.toLowerCase();
  if (d.includes('econnrefused')) {
    return 'O servidor recusou a conexão: confira a porta (8765) e se o cérebro está no ar.';
  }
  if (d.includes('etimedout') || d.includes('ehostunreach') || d.includes('enetunreach') || d.includes('timeout')) {
    return 'Não consegui alcançar o servidor: confira se o Tailscale está ligado neste PC e o IP.';
  }
  if (d.includes('enotfound') || d.includes('eai_again')) {
    return 'Não achei esse nome na rede: confira o endereço (ou use o IP do Tailscale).';
  }
  if (d.includes('404')) {
    return 'O servidor respondeu 404: o caminho do endereço deve ser /v1/ws.';
  }
  if (d.includes('401') || d.includes('4401')) {
    return 'O servidor recusou a credencial: confira o client_id e o client_secret.';
  }
  if (d.includes('secure tls') || d.includes('ssl') || d.includes('wrong version number')) {
    return 'Tentei conexão com TLS (wss://), e o servidor não fala TLS. No Tailscale use ws:// (IP 100.x.y.z).';
  }
  if (d.includes('certificate') || d.includes('self-signed') || d.includes('cert_')) {
    return 'O certificado do servidor não é confiável (wss://). Para o Tailscale use ws://.';
  }
  return null;
}
