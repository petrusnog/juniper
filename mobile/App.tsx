import React, { useCallback, useEffect, useState } from 'react';
import {
  ActivityIndicator,
  Pressable,
  StatusBar,
  StyleSheet,
  Text,
  View,
} from 'react-native';
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
import { VoiceScreen } from './src/ui/VoiceScreen';

type Tab = 'text' | 'voice';

function Home({
  credentials,
  onConfigure,
}: {
  credentials: Credentials;
  onConfigure: () => void;
}) {
  const theme = useTheme();
  const { chat, voice, state, reconnect } = useBrain(credentials);
  const [tab, setTab] = useState<Tab>('text');
  if (chat === null || voice === null) {
    return <ActivityIndicator style={styles.center} />;
  }
  return (
    <View style={styles.fill}>
      <View style={styles.fill}>
        {tab === 'text' ? (
          <ChatScreen
            chat={chat}
            connection={state}
            onReconnect={reconnect}
            onConfigure={onConfigure}
          />
        ) : (
          <VoiceScreen voice={voice} chat={chat} connection={state} />
        )}
      </View>
      <View
        style={[
          styles.tabs,
          { borderColor: theme.border, backgroundColor: theme.card },
        ]}
      >
        {(['text', 'voice'] as const).map(name => (
          <Pressable
            key={name}
            testID={`tab-${name}`}
            accessibilityRole="tab"
            onPress={() => setTab(name)}
            style={styles.tab}
          >
            <Text
              style={[
                styles.tabText,
                { color: tab === name ? theme.blue : theme.muted },
              ]}
            >
              {name === 'text' ? 'Texto' : 'Voz'}
            </Text>
          </Pressable>
        ))}
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
  tabs: { flexDirection: 'row', borderTopWidth: StyleSheet.hairlineWidth },
  tabText: { fontWeight: '600' },
  tab: { flex: 1, alignItems: 'center', padding: spacing.lg },
  home: { flex: 1, padding: spacing.xl, gap: spacing.md },
  title: { fontSize: 28, fontWeight: '700' },
  row: { flexDirection: 'row', gap: spacing.xl },
});
