import React, { useCallback, useEffect, useState } from 'react';
import {
  ActivityIndicator,
  StatusBar,
  StyleSheet,
  Text,
  View,
  Pressable,
} from 'react-native';
import { SafeAreaProvider, SafeAreaView } from 'react-native-safe-area-context';
import { useBrain } from './src/app/useBrain';
import { isProblem, statusText } from './src/connection/status';
import {
  Credentials,
  loadCredentials,
  saveCredentials,
} from './src/storage/credentials';
import { spacing } from './src/theme/tokens';
import { useTheme } from './src/theme/useTheme';
import { SetupScreen } from './src/ui/SetupScreen';

function Home({
  credentials,
  onConfigure,
}: {
  credentials: Credentials;
  onConfigure: () => void;
}) {
  const theme = useTheme();
  const { state, reconnect } = useBrain(credentials);
  return (
    <View style={styles.home}>
      <Text style={[styles.title, { color: theme.ink }]}>Juniper</Text>
      <Text
        testID="status"
        style={{
          color: isProblem(state)
            ? theme.red
            : state === 'connected'
            ? theme.green
            : theme.muted,
        }}
      >
        {statusText(state)}
      </Text>
      <View style={styles.row}>
        {state === 'replaced' || state === 'offline' ? (
          <Pressable onPress={reconnect}>
            <Text style={{ color: theme.blue }}>Reconectar</Text>
          </Pressable>
        ) : null}
        <Pressable onPress={onConfigure}>
          <Text style={{ color: theme.blue }}>Configurar</Text>
        </Pressable>
      </View>
    </View>
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
