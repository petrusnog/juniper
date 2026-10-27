import React, { useRef, useState } from 'react';
import {
  FlatList,
  KeyboardAvoidingView,
  Modal,
  Platform,
  Pressable,
  StyleSheet,
  Text,
  TextInput,
  View,
} from 'react-native';
import { ChatController } from '../chat/ChatController';
import { ChatMessage, isBusy } from '../chat/state';
import { useChat } from '../chat/useChat';
import { isProblem, statusText } from '../connection/status';
import { ConnectionState } from '../connection/BrainConnection';
import { mono, Palette, radius, spacing } from '../theme/tokens';
import { useTheme } from '../theme/useTheme';

interface Props {
  chat: ChatController;
  connection: ConnectionState;
  onReconnect: () => void;
  onConfigure: () => void;
}

function Bubble({ message, theme }: { message: ChatMessage; theme: Palette }) {
  const mine = message.role === 'user';
  const background =
    message.role === 'user'
      ? theme.blue
      : message.role === 'error'
      ? theme.card
      : message.role === 'notice'
      ? theme.background
      : theme.card;
  const color = mine
    ? '#ffffff'
    : message.role === 'error'
    ? theme.red
    : theme.ink;
  return (
    <View
      testID={`msg-${message.role}`}
      style={[
        styles.bubble,
        mine ? styles.mine : styles.theirs,
        { backgroundColor: background, borderColor: theme.border },
      ]}
    >
      <Text selectable style={[styles.body, { color }]}>
        {message.text}
        {message.streaming ? ' ▍' : ''}
      </Text>
      {message.cancelled ? (
        <Text style={[styles.small, { color: theme.muted, fontFamily: mono }]}>
          (interrompida)
        </Text>
      ) : null}
    </View>
  );
}

export function ChatScreen({
  chat,
  connection,
  onReconnect,
  onConfigure,
}: Props) {
  const theme = useTheme();
  const state = useChat(chat);
  const [text, setText] = useState('');
  const [listOpen, setListOpen] = useState(false);
  const list = useRef<FlatList<ChatMessage>>(null);
  const busy = isBusy(state);
  const connected = connection === 'connected';

  const send = () => {
    if (chat.send(text)) {
      setText('');
    }
  };

  return (
    <KeyboardAvoidingView
      style={styles.fill}
      behavior={Platform.OS === 'ios' ? 'padding' : undefined}
    >
      <View style={[styles.header, { borderColor: theme.border }]}>
        <View style={styles.fill}>
          <Text style={[styles.title, { color: theme.ink }]}>Juniper</Text>
          <Text
            testID="status"
            style={[
              styles.small,
              {
                color: isProblem(connection)
                  ? theme.red
                  : connected
                  ? theme.green
                  : theme.muted,
              },
            ]}
          >
            {statusText(connection)}
          </Text>
        </View>
        {connection === 'offline' || connection === 'replaced' ? (
          <Pressable
            testID="reconnect"
            onPress={onReconnect}
            style={styles.headerButton}
          >
            <Text style={{ color: theme.blue }}>Reconectar</Text>
          </Pressable>
        ) : null}
        <Pressable
          testID="conversations"
          onPress={() => {
            chat.refreshSessions();
            setListOpen(true);
          }}
          style={styles.headerButton}
        >
          <Text style={{ color: theme.blue }}>Conversas</Text>
        </Pressable>
        <Pressable
          testID="configure"
          onPress={onConfigure}
          style={styles.headerButton}
        >
          <Text style={{ color: theme.blue }}>Configurar</Text>
        </Pressable>
      </View>

      <FlatList
        ref={list}
        style={styles.fill}
        contentContainerStyle={styles.messages}
        data={state.messages}
        keyExtractor={m => m.key}
        renderItem={({ item }) => <Bubble message={item} theme={theme} />}
        onContentSizeChange={() =>
          list.current?.scrollToEnd({ animated: true })
        }
        ListEmptyComponent={
          <Text style={[styles.empty, { color: theme.muted }]}>
            Escreva uma mensagem para começar.
          </Text>
        }
      />

      <View
        style={[
          styles.composer,
          { borderColor: theme.border, backgroundColor: theme.card },
        ]}
      >
        <TextInput
          testID="input"
          style={[styles.input, { color: theme.ink }]}
          value={text}
          onChangeText={setText}
          placeholder="Mensagem"
          placeholderTextColor={theme.muted}
          multiline
        />
        {busy ? (
          <Pressable
            testID="cancel"
            accessibilityRole="button"
            onPress={() => chat.cancel()}
            style={[styles.send, { backgroundColor: theme.amber }]}
          >
            <Text style={styles.sendText}>Parar</Text>
          </Pressable>
        ) : (
          <Pressable
            testID="send"
            accessibilityRole="button"
            onPress={send}
            style={[styles.send, { backgroundColor: theme.blue }]}
          >
            <Text style={styles.sendText}>Enviar</Text>
          </Pressable>
        )}
      </View>

      <Modal
        visible={listOpen}
        animationType="slide"
        onRequestClose={() => setListOpen(false)}
      >
        <View style={[styles.fill, { backgroundColor: theme.background }]}>
          <View style={[styles.header, { borderColor: theme.border }]}>
            <Text style={[styles.title, styles.fill, { color: theme.ink }]}>
              Conversas
            </Text>
            <Pressable
              testID="new-conversation"
              onPress={() => {
                chat.newConversation();
                setListOpen(false);
              }}
              style={styles.headerButton}
            >
              <Text style={{ color: theme.blue }}>Nova conversa</Text>
            </Pressable>
            <Pressable
              onPress={() => setListOpen(false)}
              style={styles.headerButton}
            >
              <Text style={{ color: theme.muted }}>Fechar</Text>
            </Pressable>
          </View>
          <FlatList
            data={state.sessions ?? []}
            keyExtractor={s => s.id}
            ListEmptyComponent={
              <Text style={[styles.empty, { color: theme.muted }]}>
                {state.sessions === null
                  ? 'Carregando…'
                  : 'Nenhuma conversa ainda.'}
              </Text>
            }
            renderItem={({ item }) => (
              <Pressable
                testID={`session-${item.id}`}
                onPress={() => {
                  chat.resume(item.id);
                  setListOpen(false);
                }}
                style={[styles.session, { borderColor: theme.border }]}
              >
                <Text
                  numberOfLines={2}
                  style={[styles.body, { color: theme.ink }]}
                >
                  {item.preview || '(sem texto)'}
                </Text>
                <Text style={[styles.small, { color: theme.muted }]}>
                  {item.turns} mensagem(ns) ·{' '}
                  {item.updated_at.slice(0, 16).replace('T', ' ')}
                </Text>
              </Pressable>
            )}
          />
        </View>
      </Modal>
    </KeyboardAvoidingView>
  );
}

const styles = StyleSheet.create({
  fill: { flex: 1 },
  body: { fontSize: 16 },
  small: { fontSize: 12 },
  header: {
    flexDirection: 'row',
    alignItems: 'center',
    padding: spacing.lg,
    borderBottomWidth: StyleSheet.hairlineWidth,
    gap: spacing.sm,
  },
  title: { fontSize: 20, fontWeight: '700' },
  headerButton: { padding: spacing.sm },
  messages: { padding: spacing.lg, gap: spacing.sm, flexGrow: 1 },
  empty: { textAlign: 'center', marginTop: spacing.xl },
  bubble: {
    maxWidth: '88%',
    paddingHorizontal: spacing.lg,
    paddingVertical: spacing.md,
    borderRadius: radius.bubble,
    borderWidth: StyleSheet.hairlineWidth,
  },
  mine: { alignSelf: 'flex-end' },
  theirs: { alignSelf: 'flex-start' },
  composer: {
    flexDirection: 'row',
    alignItems: 'flex-end',
    padding: spacing.md,
    gap: spacing.sm,
    borderTopWidth: StyleSheet.hairlineWidth,
  },
  input: { flex: 1, maxHeight: 140, fontSize: 16, paddingVertical: spacing.sm },
  send: {
    paddingHorizontal: spacing.lg,
    paddingVertical: spacing.md,
    borderRadius: radius.card,
  },
  sendText: { color: '#ffffff', fontWeight: '600' },
  session: {
    padding: spacing.lg,
    borderBottomWidth: StyleSheet.hairlineWidth,
    gap: 4,
  },
});
