import React, { useState } from 'react';
import { Pressable, ScrollView, StyleSheet, View } from 'react-native';
import { Text, TextInput } from '../theme/Text';
import {
  newDeviceId,
  validateAddress,
  Credentials,
} from '../storage/credentials';
import { radius, spacing } from '../theme/tokens';
import { useTheme } from '../theme/useTheme';
import type { UpdateController } from '../update/UpdateController';
import { BrandMark } from './BrandMark';
import { UpdateSettings } from './UpdateViews';

/** `jc_id.jsk_secret` back into its two parts; anything else is an old static token. */
function splitCredential(value: string): { clientId: string; secret: string } {
  const dot = value.indexOf('.');
  if (value.startsWith('jc_') && dot > 0) {
    return { clientId: value.slice(0, dot), secret: value.slice(dot + 1) };
  }
  return { clientId: '', secret: value };
}

interface Props {
  initial?: Credentials | null;
  onSave: (credentials: Credentials) => void;
  onCancel?: () => void;
  /** The self-update (only once the app is configured: it talks to the brain's address). */
  updater?: UpdateController | null;
}

/**
 * First run: the server address (Tailscale) and the credential, typed by hand (ADR-022, section 3):
 * the `client_id` and the `client_secret` the owner got when registering (ADR-027). Leaving the
 * client id empty keeps the old way: the secret field then holds the server's static token.
 */
export function SetupScreen({ initial, onSave, onCancel, updater }: Props) {
  const theme = useTheme();
  const [host, setHost] = useState(initial?.host ?? '');
  const [port, setPort] = useState(String(initial?.port ?? 8765));
  const stored = splitCredential(initial?.token ?? '');
  const [clientId, setClientId] = useState(stored.clientId);
  const [token, setToken] = useState(stored.secret);
  const [name, setName] = useState(initial?.deviceName ?? 'Celular');
  const [error, setError] = useState<string | null>(null);

  const submit = () => {
    const portNumber = Number(port);
    const problem = validateAddress(host, portNumber);
    if (problem) {
      return setError(problem);
    }
    if (token.trim().length < 8) {
      return setError('Cole o client_secret (ou o token) completo do servidor.');
    }
    setError(null);
    const id = clientId.trim();
    onSave({
      host: host.trim(),
      port: portNumber,
      token: id ? `${id}.${token.trim()}` : token.trim(),
      deviceId: initial?.deviceId ?? newDeviceId(),
      deviceName: name.trim() || 'Celular',
      shortcutNotification: initial?.shortcutNotification,
    });
  };

  const input = [
    styles.input,
    {
      backgroundColor: theme.card,
      color: theme.ink,
      borderColor: theme.border,
    },
  ];
  return (
    <ScrollView
      style={{ backgroundColor: theme.background }}
      contentContainerStyle={styles.content}
      keyboardShouldPersistTaps="handled"
    >
      <View style={styles.brand}>
        <BrandMark size="large" />
      </View>
      <Text style={[styles.title, { color: theme.ink }]}>
        Conectar à Juniper
      </Text>
      <Text style={[styles.help, { color: theme.muted }]}>
        Use o endereço do servidor dentro do Tailscale (com o app do Tailscale
        ligado) e o client_id e o client_secret que o dono cadastrou para você.
      </Text>
      <Text style={[styles.label, { color: theme.ink }]}>Endereço</Text>
      <TextInput
        testID="host"
        style={input}
        value={host}
        onChangeText={setHost}
        placeholder="100.x.y.z ou nome.ts.net"
        placeholderTextColor={theme.muted}
        autoCapitalize="none"
        autoCorrect={false}
      />
      <Text style={[styles.label, { color: theme.ink }]}>Porta</Text>
      <TextInput
        testID="port"
        style={input}
        value={port}
        onChangeText={setPort}
        keyboardType="number-pad"
      />
      <Text style={[styles.label, { color: theme.ink }]}>Client ID</Text>
      <TextInput
        testID="client-id"
        style={input}
        value={clientId}
        onChangeText={setClientId}
        autoCapitalize="none"
        autoCorrect={false}
        placeholder="jc_..."
        placeholderTextColor={theme.muted}
      />
      <Text style={[styles.label, { color: theme.ink }]}>
        Client secret (ou token)
      </Text>
      <TextInput
        testID="token"
        style={input}
        value={token}
        onChangeText={setToken}
        secureTextEntry
        autoCapitalize="none"
        autoCorrect={false}
        placeholder="cole aqui"
        placeholderTextColor={theme.muted}
      />
      <Text style={[styles.label, { color: theme.ink }]}>
        Nome deste aparelho
      </Text>
      <TextInput
        testID="name"
        style={input}
        value={name}
        onChangeText={setName}
      />
      {error ? (
        <Text testID="error" style={[styles.error, { color: theme.red }]}>
          {error}
        </Text>
      ) : null}
      <View style={styles.row}>
        <Pressable
          testID="save"
          accessibilityRole="button"
          onPress={submit}
          style={[styles.button, { backgroundColor: theme.accent }]}
        >
          <Text style={styles.buttonText}>Salvar e conectar</Text>
        </Pressable>
        {onCancel ? (
          <Pressable
            accessibilityRole="button"
            onPress={onCancel}
            style={styles.button}
          >
            <Text style={{ color: theme.muted }}>Cancelar</Text>
          </Pressable>
        ) : null}
      </View>
      {updater ? <UpdateSettings updater={updater} /> : null}
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  content: { padding: spacing.xl, gap: spacing.sm },
  brand: { alignItems: 'center', marginBottom: spacing.lg },
  title: { fontSize: 24, fontWeight: '700' },
  help: { fontSize: 14, marginBottom: spacing.md },
  label: { fontSize: 14, fontWeight: '600', marginTop: spacing.sm },
  input: {
    borderWidth: 1,
    borderRadius: radius.card,
    paddingHorizontal: spacing.lg,
    paddingVertical: spacing.md,
    fontSize: 16,
  },
  error: { marginTop: spacing.sm, fontSize: 14 },
  row: { flexDirection: 'row', gap: spacing.md, marginTop: spacing.lg },
  button: {
    paddingHorizontal: spacing.xl,
    paddingVertical: spacing.md,
    borderRadius: radius.card,
  },
  buttonText: { color: '#000000', fontWeight: '700' },
});
