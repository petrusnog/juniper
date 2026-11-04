/**
 * The window (ADR-033): text only. Everything the brain or the model wrote is put in with
 * `textContent`, never `innerHTML`; links are plain text. It reaches the main process only
 * through the bridge in `window.juniper`.
 */
import type { JuniperBridge, UiSnapshot } from '../shared/ipc';

declare global {
  interface Window {
    juniper: JuniperBridge;
  }
}

const $ = <T extends HTMLElement>(id: string): T => document.getElementById(id) as T;
const STATUS: Record<string, string> = {
  idle: 'desconectado',
  connecting: 'conectando…',
  connected: 'online',
  offline: 'Juniper indisponível; tentando de novo',
  unauthorized: 'credencial recusada',
  incompatible: 'versão incompatível com o cérebro',
  replaced: 'outra janela assumiu este dispositivo',
};

function render(s: UiSnapshot): void {
  $('status').textContent = STATUS[s.connection] ?? s.connection;
  $<HTMLInputElement>('pause').checked = s.filesPaused;
  $('login').hidden = s.hasCredential && s.connection !== 'unauthorized';
  $('composer').hidden = !s.hasCredential;
  const box = $('messages');
  box.replaceChildren(
    ...s.chat.messages.map(m => {
      const div = document.createElement('div');
      div.className = `m ${m.role}`;
      div.textContent = m.text;
      return div;
    }),
  );
  box.scrollTop = box.scrollHeight;
  $('activity').textContent = s.activity
    .slice(-5)
    .map(a => `${a.tool}: ${a.outcome}${a.code ? ` (${a.code})` : ''}`)
    .join('\n');
}

async function main(): Promise<void> {
  const api = window.juniper;
  api.onEvent(render);
  render(await api.snapshot());
  $('composer').addEventListener('submit', async e => {
    e.preventDefault();
    const input = $<HTMLInputElement>('text');
    if (await api.send(input.value)) input.value = ''; // the text stays if it could not go
  });
  $('stop').addEventListener('click', () => void api.cancel());
  $('new').addEventListener('click', () => void api.newChat());
  $('logout').addEventListener('click', () => void api.logout());
  $('pause').addEventListener('change', e => void api.pauseFiles((e.target as HTMLInputElement).checked));
  $('login').addEventListener('submit', async e => {
    e.preventDefault();
    const secret = $<HTMLInputElement>('secret');
    const result = await api.login({
      url: $<HTMLInputElement>('url').value,
      clientId: $<HTMLInputElement>('cid').value,
      clientSecret: secret.value,
    });
    secret.value = ''; // the renderer never keeps the secret
    $('login-error').textContent = result.ok ? '' : (result.error ?? 'não consegui conectar');
  });
}

void main();
