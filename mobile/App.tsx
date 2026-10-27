import React, { useCallback, useEffect, useState } from 'react';
import { ActivityIndicator, StatusBar, StyleSheet } from 'react-native';
import { SafeAreaProvider, SafeAreaView } from 'react-native-safe-area-context';
import { useBrain } from './src/app/useBrain';
import {
  Credentials,
  loadCredentials,
  saveCredentials,
} from './src/storage/credentials';
import { spacing } from './src/theme/tokens';
import { useTheme } from './src/theme/useTheme';
import { ChatScreen } from './src/ui/ChatScreen';
import { SetupScreen } from './src/ui/SetupScreen';

function Home({
  credentials,
  onConfigure,
}: {
  credentials: Credentials;
  onConfigure: () => void;
}) {
  const { chat, state, reconnect } = useBrain(credentials);
  if (chat === null) {
    return <ActivityIndicator style={styles.center} />;
  }
  return (
    <ChatScreen
      chat={chat}
      connection={state}
      onReconnect={reconnect}
      onConfigure={onConfigure}
    />
  );
}

function Root() {
  const theme = useTheme();
  const [loading, setLoading] = useState(true);
  const [credentials, setCredentials] = useState<Credentials | null>(null);
  const [editing, setEditing] = useState(false);

  useEffect(() => {
    loadCredentials().then(found => {
      setCredentials(found);
      setLoading(false);
    });
  }, []);

  const save = useCallback(async (next: Credentials) => {
    await saveCredentials(next);
    setCredentials(next);
    setEditing(false);
  }, []);

  if (loading) {
    return <ActivityIndicator style={styles.center} color={theme.blue} />;
  }
  if (credentials === null || editing) {
    return (
      <SetupScreen
        initial={credentials}
        onSave={save}
        onCancel={credentials ? () => setEditing(false) : undefined}
      />
    );
  }
  return (
    <Home credentials={credentials} onConfigure={() => setEditing(true)} />
  );
}

export default function App() {
  const theme = useTheme();
  return (
    <SafeAreaProvider>
      <StatusBar
        barStyle={
          theme.background === '#14171f' ? 'light-content' : 'dark-content'
        }
      />
      <SafeAreaView
        style={[styles.fill, { backgroundColor: theme.background }]}
      >
        <Root />
      </SafeAreaView>
    </SafeAreaProvider>
  );
}

const styles = StyleSheet.create({
  fill: { flex: 1 },
  center: { flex: 1 },
  home: { flex: 1, padding: spacing.xl, gap: spacing.md },
  title: { fontSize: 28, fontWeight: '700' },
  row: { flexDirection: 'row', gap: spacing.xl },
});
