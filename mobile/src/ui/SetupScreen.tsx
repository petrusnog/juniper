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

interface Props {
  initial?: Credentials | null;
  onSave: (credentials: Credentials) => void;
  onCancel?: () => void;
}

/** First run: the server address (Tailscale) and the token, pasted by hand (ADR-022, section 3). */
export function SetupScreen({ initial, onSave, onCancel }: Props) {
  const theme = useTheme();
  const [host, setHost] = useState(initial?.host ?? '');
  const [port, setPort] = useState(String(initial?.port ?? 8765));
  const [token, setToken] = useState(initial?.token ?? '');
  const [name, setName] = useState(initial?.deviceName ?? 'Celular');
  const [error, setError] = useState<string | null>(null);

  const submit = () => {
    const portNumber = Number(port);
    const problem = validateAddress(host, portNumber);
    if (problem) {
      return setError(problem);
    }
    if (token.trim().length < 8) {
      return setError('Cole o token completo do servidor.');
    }
    setError(null);
    onSave({
      host: host.trim(),
      port: portNumber,
      token: token.trim(),
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
      <Text style={[styles.title, { color: theme.ink }]}>
        Conectar à Juniper
      </Text>
      <Text style={[styles.help, { color: theme.muted }]}>
        Use o endereço do servidor dentro do Tailscale (com o app do Tailscale
        ligado) e o token dos clientes.
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
      <Text style={[styles.label, { color: theme.ink }]}>Token</Text>
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
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  content: { padding: spacing.xl, gap: spacing.sm },
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
