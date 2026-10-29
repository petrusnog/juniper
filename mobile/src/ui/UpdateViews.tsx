import React from 'react';
import { Pressable, StyleSheet, View } from 'react-native';
import { Text } from '../theme/Text';
import { radius, spacing } from '../theme/tokens';
import { useTheme } from '../theme/useTheme';
import type { UpdateController } from '../update/UpdateController';
import { useUpdate } from '../update/useUpdate';

function progressText(percent: number): string {
  return `Baixando a atualização… ${Math.round(percent * 100)}%`;
}

/** A strip above the tabs while a newer version is available or being installed. */
export function UpdateBanner({ updater }: { updater: UpdateController }) {
  const theme = useTheme();
  const state = useUpdate(updater);
  if (
    state.phase !== 'available' &&
    state.phase !== 'downloading' &&
    state.phase !== 'installing'
  ) {
    return null;
  }
  const message =
    state.phase === 'available'
      ? `Nova versão ${state.available?.versionName ?? ''} disponível.`
      : state.phase === 'downloading'
      ? progressText(state.progress)
      : 'Confirme a instalação na tela do Android.';
  return (
    <View
      testID="update-banner"
      style={[
        styles.banner,
        { borderColor: theme.accent, backgroundColor: theme.card },
      ]}
    >
      <View style={styles.fill}>
        <Text style={{ color: theme.ink }}>{message}</Text>
        {state.notice && state.phase === 'available' ? (
          <Text
            testID="update-banner-notice"
            style={[styles.small, { color: theme.amber }]}
          >
            {state.notice}
          </Text>
        ) : null}
      </View>
      {state.phase === 'available' ? (
        <Pressable
          testID="update-install"
          accessibilityRole="button"
          onPress={() => {
            updater.install().catch(() => undefined);
          }}
          style={[styles.button, { backgroundColor: theme.accent }]}
        >
          <Text style={[styles.buttonText, { color: theme.onAccent }]}>
            Atualizar
          </Text>
        </Pressable>
      ) : null}
    </View>
  );
}

/** The «Atualizações» section of the settings: the version and the manual check button. */
export function UpdateSettings({ updater }: { updater: UpdateController }) {
  const theme = useTheme();
  const state = useUpdate(updater);
  const busy =
    state.phase === 'checking' ||
    state.phase === 'downloading' ||
    state.phase === 'installing';
  const status =
    state.phase === 'checking'
      ? 'Procurando…'
      : state.phase === 'current'
      ? 'Você está na versão mais recente.'
      : state.phase === 'available'
      ? `Nova versão ${state.available?.versionName ?? ''} disponível.`
      : state.phase === 'downloading'
      ? progressText(state.progress)
      : state.phase === 'installing'
      ? 'Confirme a instalação na tela do Android.'
      : null;
  return (
    <View style={styles.section}>
      <Text style={[styles.title, { color: theme.ink }]}>Atualizações</Text>
      <Text testID="update-installed" style={{ color: theme.muted }}>
        {state.installed
          ? `Versão instalada: ${state.installed.name} (código ${state.installed.code})`
          : 'Versão instalada: toque em Buscar para ver.'}
      </Text>
      {status ? (
        <Text testID="update-status" style={{ color: theme.green }}>
          {status}
        </Text>
      ) : null}
      {state.notice ? (
        <Text
          testID="update-notice"
          style={{ color: state.phase === 'error' ? theme.red : theme.amber }}
        >
          {state.notice}
        </Text>
      ) : null}
      <View style={styles.row}>
        <Pressable
          testID="update-check"
          accessibilityRole="button"
          disabled={busy}
          onPress={() => {
            updater.check().catch(() => undefined);
          }}
          style={[
            styles.button,
            styles.outlined,
            { borderColor: theme.accent },
            busy ? styles.disabled : null,
          ]}
        >
          <Text style={{ color: theme.accent }}>Buscar atualizações</Text>
        </Pressable>
        {state.phase === 'available' ? (
          <Pressable
            testID="update-install-settings"
            accessibilityRole="button"
            onPress={() => {
              updater.install().catch(() => undefined);
            }}
            style={[styles.button, { backgroundColor: theme.accent }]}
          >
            <Text style={[styles.buttonText, { color: theme.onAccent }]}>
              Atualizar
            </Text>
          </Pressable>
        ) : null}
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  fill: { flex: 1 },
  banner: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: spacing.md,
    padding: spacing.md,
    borderTopWidth: 1,
  },
  small: { fontSize: 12 },
  section: { gap: spacing.sm, marginTop: spacing.xl },
  title: { fontSize: 16, fontWeight: '700' },
  row: { flexDirection: 'row', gap: spacing.md, marginTop: spacing.sm },
  button: {
    paddingHorizontal: spacing.lg,
    paddingVertical: spacing.md,
    borderRadius: radius.card,
  },
  buttonText: { fontWeight: '700' },
  outlined: { borderWidth: 1 },
  disabled: { opacity: 0.5 },
});
