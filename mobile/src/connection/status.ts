import { ConnectionState } from './BrainConnection';

/** What the status line says for each connection state (PT-BR, for the user). */
export function statusText(state: ConnectionState): string {
  switch (state) {
    case 'connected':
      return 'Conectada';
    case 'connecting':
      return 'Conectando…';
    case 'offline':
      return 'Sem conexão com o servidor. Confira se o Tailscale está ligado; tentando de novo…';
    case 'unauthorized':
      return 'O servidor recusou o token. Toque em «Configurar» e cole o token de novo.';
    case 'incompatible':
      return 'Este app e o servidor falam versões diferentes do protocolo. Atualize o app.';
    case 'replaced':
      return 'Outra conexão deste aparelho assumiu. Toque para reconectar.';
    default:
      return 'Desconectada';
  }
}

export function isProblem(state: ConnectionState): boolean {
  return ['offline', 'unauthorized', 'incompatible', 'replaced'].includes(
    state,
  );
}
