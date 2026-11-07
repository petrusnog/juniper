/**
 * The window (ADR-033): the chat in Juniper's terminal look, with the same bubbles as the phone
 * app. Everything the brain or the model wrote goes in with `textContent`, never `innerHTML`.
 * It reaches the main process only through the bridge in `window.juniper`.
 */
import { isBusy } from '../core/chat/state';
import type { ChatMessage } from '../core/chat/state';
import type { JuniperBridge, UiSnapshot } from '../shared/ipc';
import { startAudioEngine } from './audio';

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
const PROBLEM = new Set(['unauthorized', 'incompatible', 'replaced']);
const VOICE_PHASE: Record<string, string> = {
  starting: 'abrindo a conversa de voz…',
  listening: 'ouvindo',
  thinking: 'pensando…',
  speaking: 'falando',
};

/** One chat bubble. The streaming caret and "(interrompida)" are the phone app's. */
function bubble(message: ChatMessage): HTMLElement {
  const div = document.createElement('div');
  div.className = `bubble ${message.role}`;
  div.dataset.role = message.role;
  div.textContent = message.text + (message.streaming ? ' ▍' : '');
  if (message.cancelled) {
    const small = document.createElement('small');
    small.textContent = '(interrompida)';
    div.append(small);
  }
  return div;
}

function formatDate(iso: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime())
    ? ''
    : d.toLocaleString('pt-BR', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' });
}

let lastSignature = '';

function renderMessages(s: UiSnapshot): void {
  const box = $('messages');
  const nearBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 80;
  if (s.chat.messages.length === 0) {
    const p = document.createElement('p');
    p.className = 'empty';
    p.textContent = 'Escreva uma mensagem para começar.';
    box.replaceChildren(p);
    return;
  }
  box.replaceChildren(...s.chat.messages.map(bubble));
  const signature = `${s.chat.messages.length}:${s.chat.messages.at(-1)?.text.length ?? 0}`;
  if (nearBottom || signature !== lastSignature) box.scrollTop = box.scrollHeight;
  lastSignature = signature;
}

function renderSessions(s: UiSnapshot, onPick: (id: string) => void): void {
  const list = $('session-list');
  const sessions = s.chat.sessions ?? [];
  if (sessions.length === 0) {
    const li = document.createElement('li');
    li.className = 'meta';
    li.textContent = 'Nenhuma conversa ainda.';
    list.replaceChildren(li);
    return;
  }
  list.replaceChildren(
    ...sessions.map(item => {
      const li = document.createElement('li');
      if (item.id === s.chat.sessionId) li.classList.add('current');
      const preview = document.createElement('div');
      preview.className = 'preview';
      preview.textContent = item.preview || '(sem texto)';
      const meta = document.createElement('div');
      meta.className = 'meta';
      meta.textContent = `${item.turns} ${item.turns === 1 ? 'turno' : 'turnos'} · ${formatDate(item.updated_at)}`;
      li.append(preview, meta);
      li.addEventListener('click', () => onPick(item.id));
      return li;
    }),
  );
}

function render(s: UiSnapshot, onPick: (id: string) => void): void {
  // A refused credential goes back to the form, so it can be fixed.
  document.body.dataset.view = s.hasCredential && s.connection !== 'unauthorized' ? 'chat' : 'login';

  const status = $('status');
  status.textContent = STATUS[s.connection] ?? s.connection;
  status.title = s.connectionDetail ?? '';
  status.className = `status ${s.connection === 'connected' ? 'ok' : PROBLEM.has(s.connection) ? 'problem' : ''}`;
  const detail = [s.connectionHint, s.connectionDetail ? `Detalhe: ${s.connectionDetail}` : null].filter(Boolean).join(' ');
  $('connection-detail').hidden = s.connection === 'connected' || detail === '';
  $('connection-detail').textContent = detail;

  $<HTMLInputElement>('pause').checked = s.filesPaused;
  $('version').textContent = `versão ${s.version}`;

  const u = s.update;
  $('update-line').hidden = u.phase === 'none';
  $<HTMLButtonElement>('update-apply').hidden = u.phase !== 'available';
  $('update-text').textContent =
    u.phase === 'available'
      ? `Há uma versão nova (${u.version}). Vou atualizar quando você não estiver conversando.`
      : u.phase === 'working'
        ? { downloading: `Baixando a versão ${u.version}…`, verifying: 'Conferindo a assinatura…', installing: 'Instalando e reiniciando…' }[u.step]
        : u.phase === 'error'
          ? u.message
          : '';

  const v = s.voice;
  $('voice').textContent = v.phase === 'off' ? 'Voz' : 'Encerrar voz';
  $('voice-line').hidden = v.phase === 'off' && v.notice === null;
  $('voice-line').textContent =
    v.phase === 'off' ? (v.notice ?? '') : `${VOICE_PHASE[v.phase] ?? v.phase}${v.userText ? ` — «${v.userText}»` : ''}`;

  const busy = isBusy(s.chat);
  $('send').hidden = busy;
  $('stop').hidden = !busy;

  $('activity').textContent = s.activity
    .slice(-3)
    .map(a => `${a.tool}: ${a.outcome}${a.code ? ` (${a.code})` : ''}`)
    .join('\n');

  renderMessages(s);
  renderSessions(s, onPick);
}

async function main(): Promise<void> {
  const api = window.juniper;
  const closeDrawer = () => {
    $('drawer').hidden = true;
  };
  const pick = (id: string) => {
    void api.resume(id);
    closeDrawer();
  };
  api.onEvent(s => render(s, pick));
  startAudioEngine(api);
  render(await api.snapshot(), pick);

  const text = $<HTMLTextAreaElement>('text');
  const grow = () => {
    text.style.height = 'auto';
    text.style.height = `${Math.min(text.scrollHeight, 140)}px`;
  };
  const submit = async () => {
    if (await api.send(text.value)) {
      text.value = ''; // the text stays if it could not go
      grow();
    }
  };
  $('composer').addEventListener('submit', e => {
    e.preventDefault();
    void submit();
  });
  text.addEventListener('input', grow);
  text.addEventListener('keydown', e => {
    if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) {
      e.preventDefault();
      void submit();
    }
  });
  $('stop').addEventListener('click', () => void api.cancel());

  $('conversations').addEventListener('click', () => {
    const drawer = $('drawer');
    drawer.hidden = !drawer.hidden;
    if (!drawer.hidden) void api.sessions();
  });
  $('drawer-close').addEventListener('click', closeDrawer);
  $('new').addEventListener('click', () => {
    void api.newChat();
    closeDrawer();
  });
  $('options-toggle').addEventListener('click', () => {
    const panel = $('options');
    panel.hidden = !panel.hidden;
    $('options-toggle').setAttribute('aria-expanded', String(!panel.hidden));
  });
  $('logout').addEventListener('click', () => void api.logout());
  $('pause').addEventListener('change', e => void api.pauseFiles((e.target as HTMLInputElement).checked));
  $('headset').addEventListener('change', e =>
    void api.audioEvent({ kind: 'headset', value: (e.target as HTMLInputElement).checked }),
  );
  $('update-apply').addEventListener('click', () => void api.updateApply());
  $('voice').addEventListener('click', () => {
    void api.snapshot().then(async s => {
      if (s.voice.phase === 'off') await api.voiceStart();
      else await api.voiceEnd();
    });
  });
  $('voice-line').addEventListener('click', () => void api.voiceInterrupt());

  $('login').addEventListener('submit', async e => {
    e.preventDefault();
    const secret = $<HTMLInputElement>('secret');
    $('login-error').textContent = '';
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
