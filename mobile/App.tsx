import React, { useCallback, useEffect, useMemo, useState } from 'react';
import {
  ActivityIndicator,
  Linking,
  Pressable,
  StatusBar,
  StyleSheet,
  View,
} from 'react-native';
import { Text } from './src/theme/Text';
import { SafeAreaProvider, SafeAreaView } from 'react-native-safe-area-context';
import { useBrain } from './src/app/useBrain';
import { isLaunchUrl } from './src/launch/VoiceLauncher';
import { nativeLauncher } from './src/launch/nativeLauncher';
import {
  Credentials,
  httpBase,
  loadCredentials,
  saveCredentials,
} from './src/storage/credentials';
import { spacing } from './src/theme/tokens';
import { useTheme } from './src/theme/useTheme';
import { BrandMark } from './src/ui/BrandMark';
import { ChatScreen } from './src/ui/ChatScreen';
import { SetupScreen } from './src/ui/SetupScreen';
import { UpdateBanner } from './src/ui/UpdateViews';
import { VoiceScreen } from './src/ui/VoiceScreen';
import { nativeUpdate } from './src/update/nativeUpdate';
import { UpdateController } from './src/update/UpdateController';

type Tab = 'text' | 'voice';

/** The link that opened the app is handled once per process, not on every remount. */
let initialLinkHandled = false;

function Home({
  credentials,
  updater,
  onConfigure,
}: {
  credentials: Credentials;
  updater: UpdateController;
  onConfigure: () => void;
}) {
  const theme = useTheme();
  const { chat, voice, launcher, state, reconnect } = useBrain(credentials);
  const [tab, setTab] = useState<Tab>('text');
  const [shortcut, setShortcut] = useState(
    credentials.shortcutNotification === true,
  );
  const [shortcutNotice, setShortcutNotice] = useState<string | null>(null);

  // juniper://voz (shortcut, notification button, assistant): open the voice and start talking.
  useEffect(() => {
    if (launcher === null) {
      return undefined;
    }
    const open = (url: string | null | undefined) => {
      if (isLaunchUrl(url)) {
        setTab('voice');
        launcher.trigger().catch(() => undefined);
      }
    };
    if (!initialLinkHandled) {
      initialLinkHandled = true;
      Linking.getInitialURL()
        .then(open)
        .catch(() => undefined);
    }
    const sub = Linking.addEventListener('url', event => open(event.url));
    return () => sub.remove();
  }, [launcher]);

  const changeShortcut = useCallback(
    async (on: boolean) => {
      setShortcutNotice(null);
      if (on) {
        const shown = await nativeLauncher.show().catch(() => false);
        if (!shown) {
          setShortcutNotice(
            'Permita as notificações do app para ligar o botão.',
          );
          return;
        }
      } else {
        await nativeLauncher.hide().catch(() => undefined);
      }
      setShortcut(on);
      saveCredentials({ ...credentials, shortcutNotification: on }).catch(
        () => undefined,
      );
    },
    [credentials],
  );

  if (chat === null || voice === null || launcher === null) {
    return <ActivityIndicator style={styles.center} />;
  }
  return (
    <View style={styles.fill}>
      <View
        style={[
          styles.brandBar,
          { borderColor: theme.border, backgroundColor: theme.background },
        ]}
      >
        <BrandMark />
      </View>
      <View style={styles.fill}>
        {tab === 'text' ? (
          <ChatScreen
            chat={chat}
            connection={state}
            onReconnect={reconnect}
            onConfigure={onConfigure}
          />
        ) : (
          <VoiceScreen
            voice={voice}
            chat={chat}
            connection={state}
            shortcut={shortcut}
            shortcutNotice={shortcutNotice}
            onShortcutChange={on => {
              changeShortcut(on).catch(() => undefined);
            }}
          />
        )}
      </View>
      <UpdateBanner updater={updater} />
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
                { color: tab === name ? theme.accent : theme.muted },
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

  // One updater per address and token; the automatic check runs once, when it is created.
  const updater = useMemo(
    () =>
      credentials === null
        ? null
        : new UpdateController(
            { base: httpBase(credentials), token: credentials.token },
            nativeUpdate,
          ),
    [credentials?.host, credentials?.port, credentials?.token], // eslint-disable-line react-hooks/exhaustive-deps
  );
  useEffect(() => {
    updater?.check({ silent: true }).catch(() => undefined);
    return () => updater?.dispose();
  }, [updater]);

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
    return <ActivityIndicator style={styles.center} color={theme.accent} />;
  }
  if (credentials === null || editing) {
    return (
      <SetupScreen
        initial={credentials}
        onSave={save}
        onCancel={credentials ? () => setEditing(false) : undefined}
        updater={credentials ? updater : null}
      />
    );
  }
  if (updater === null) {
    return <ActivityIndicator style={styles.center} color={theme.accent} />;
  }
  return (
    <Home
      credentials={credentials}
      updater={updater}
      onConfigure={() => setEditing(true)}
    />
  );
}

export default function App() {
  const theme = useTheme();
  return (
    <SafeAreaProvider>
      <StatusBar barStyle="light-content" />
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
  brandBar: {
    paddingHorizontal: spacing.lg,
    paddingVertical: spacing.sm,
    borderBottomWidth: StyleSheet.hairlineWidth,
  },
  tabs: { flexDirection: 'row', borderTopWidth: StyleSheet.hairlineWidth },
  tabText: { fontWeight: '700', letterSpacing: 1 },
  tab: { flex: 1, alignItems: 'center', padding: spacing.lg },
  home: { flex: 1, padding: spacing.xl, gap: spacing.md },
  title: { fontSize: 28, fontWeight: '700' },
  row: { flexDirection: 'row', gap: spacing.xl },
});
