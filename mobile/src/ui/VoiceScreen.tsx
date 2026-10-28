import React from 'react';
import { Pressable, ScrollView, StyleSheet, Text, View } from 'react-native';
import { ChatController } from '../chat/ChatController';
import { useChat } from '../chat/useChat';
import { ConnectionState } from '../connection/BrainConnection';
import { radius, spacing } from '../theme/tokens';
import { useTheme } from '../theme/useTheme';
import { VoiceController, VoicePhase } from '../voice/VoiceController';
import { useVoice } from '../voice/useVoice';

interface Props {
  voice: VoiceController;
  chat: ChatController;
  connection: ConnectionState;
}

const LABEL: Record<VoicePhase, string> = {
  off: 'Toque para conversar',
  starting: 'Abrindo a conversa…',
  listening: 'Ouvindo',
  thinking: 'Pensando…',
  speaking: 'Falando',
};

/** The voice chat: one big button, the state and the captions of both sides. */
export function VoiceScreen({ voice, chat, connection }: Props) {
  const theme = useTheme();
  const state = useVoice(voice);
  const chatState = useChat(chat);
  const reply = [...chatState.messages]
    .reverse()
    .find(m => m.role === 'assistant');
  const active = state.phase !== 'off';
  const blocked = !active && connection !== 'connected';
  const color = !active
    ? theme.blue
    : state.phase === 'speaking'
    ? theme.violet
    : state.phase === 'thinking'
    ? theme.amber
    : theme.green;

  return (
    <View style={styles.fill}>
      <ScrollView contentContainerStyle={styles.captions}>
        <Text testID="phase" style={[styles.phase, { color: theme.ink }]}>
          {LABEL[state.phase]}
        </Text>
        {state.userText ? (
          <View
            style={[
              styles.caption,
              { backgroundColor: theme.card, borderColor: theme.border },
            ]}
          >
            <Text style={[styles.who, { color: theme.muted }]}>Você</Text>
            <Text
              testID="user-caption"
              style={[styles.text, { color: theme.ink }]}
            >
              {state.userText}
            </Text>
          </View>
        ) : null}
        {reply && reply.text ? (
          <View
            style={[
              styles.caption,
              { backgroundColor: theme.card, borderColor: theme.border },
            ]}
          >
            <Text style={[styles.who, { color: theme.muted }]}>Juniper</Text>
            <Text
              testID="reply-caption"
              style={[styles.text, { color: theme.ink }]}
            >
              {reply.text}
            </Text>
          </View>
        ) : null}
        {active && (state.stt === 'local' || state.tts === 'local') ? (
          <Text
            testID="fallback"
            style={[styles.small, { color: theme.amber }]}
          >
            {state.stt === 'local'
              ? 'Ouvindo com o reconhecimento do aparelho. '
              : ''}
            {state.tts === 'local' ? 'Falando com a voz do aparelho.' : ''}
          </Text>
        ) : null}
        {state.notice ? (
          <Text testID="notice" style={[styles.small, { color: theme.red }]}>
            {state.notice}
          </Text>
        ) : null}
      </ScrollView>

      <View style={styles.controls}>
        {state.phase === 'speaking' || state.phase === 'thinking' ? (
          <Pressable
            testID="interrupt"
            onPress={() => voice.interrupt()}
            style={[styles.secondary, { borderColor: theme.border }]}
          >
            <Text style={{ color: theme.ink }}>Parar de falar</Text>
          </Pressable>
        ) : null}
        <Pressable
          testID="toggle"
          accessibilityRole="button"
          accessibilityLabel={
            active ? 'Encerrar a conversa de voz' : 'Começar a conversa de voz'
          }
          disabled={blocked}
          onPress={() => (active ? voice.end() : voice.start())}
          style={[
            styles.big,
            { backgroundColor: color },
            blocked && styles.blocked,
          ]}
        >
          <Text style={styles.bigText}>{active ? 'Encerrar' : 'Falar'}</Text>
        </Pressable>
        {blocked ? (
          <Text style={[styles.small, { color: theme.muted }]}>
            Sem conexão com a Juniper.
          </Text>
        ) : null}
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  fill: { flex: 1 },
  captions: { padding: spacing.xl, gap: spacing.md },
  phase: { fontSize: 22, fontWeight: '700', textAlign: 'center' },
  caption: {
    padding: spacing.lg,
    borderRadius: radius.card,
    borderWidth: StyleSheet.hairlineWidth,
    gap: 4,
  },
  who: { fontSize: 12, fontWeight: '600' },
  text: { fontSize: 18 },
  small: { fontSize: 13, textAlign: 'center' },
  controls: { alignItems: 'center', padding: spacing.xl, gap: spacing.md },
  big: {
    width: 132,
    height: 132,
    borderRadius: 66,
    alignItems: 'center',
    justifyContent: 'center',
  },
  blocked: { opacity: 0.4 },
  bigText: { color: '#ffffff', fontSize: 20, fontWeight: '700' },
  secondary: {
    paddingHorizontal: spacing.xl,
    paddingVertical: spacing.md,
    borderRadius: radius.card,
    borderWidth: 1,
  },
});
