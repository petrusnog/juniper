import React, { useEffect, useRef, useState } from 'react';
import {
  AccessibilityInfo,
  Animated,
  Image,
  StyleSheet,
  View,
} from 'react-native';
import { Text } from '../theme/Text';
import { spacing } from '../theme/tokens';
import { useTheme } from '../theme/useTheme';

// The logo (a transparent PNG made from branding/juniper-neon.png).
const logo = require('../../branding/logo.png');

/** How long the cursor stays on, and then off (a terminal cursor: no fading). */
export const BLINK_MS = 550;

const SIZES = {
  small: { logo: 32, text: 20 },
  large: { logo: 96, text: 30 },
} as const;

interface Props {
  /** `small` is the bar at the top of the app; `large` is the first-run screen. */
  size?: keyof typeof SIZES;
  /** Turn the blinking off (the cursor stays visible). */
  blink?: boolean;
}

/** True while the system asks for less motion (Android: "remove animations"). */
export function useReduceMotion(): boolean {
  const [reduce, setReduce] = useState(false);
  useEffect(() => {
    let alive = true;
    AccessibilityInfo.isReduceMotionEnabled()
      .then(on => alive && setReduce(on))
      .catch(() => undefined);
    const sub = AccessibilityInfo.addEventListener(
      'reduceMotionChanged',
      setReduce,
    );
    return () => {
      alive = false;
      sub.remove();
    };
  }, []);
  return reduce;
}

/**
 * The mark of the Juniper, the same in every interface: the logo, the name at its right and a
 * blinking cursor (`_`). With "reduce motion" on, the cursor stays still.
 */
export function BrandMark({ size = 'small', blink = true }: Props) {
  const theme = useTheme();
  const reduceMotion = useReduceMotion();
  const opacity = useRef(new Animated.Value(1)).current;
  const animate = blink && !reduceMotion;
  const dimension = SIZES[size];

  useEffect(() => {
    opacity.setValue(1);
    if (!animate) {
      return undefined;
    }
    // Driven from JS on purpose: two tiny updates a second, and it can be tested with fake timers.
    const loop = Animated.loop(
      Animated.sequence([
        Animated.delay(BLINK_MS),
        Animated.timing(opacity, {
          toValue: 0,
          duration: 0,
          useNativeDriver: false,
        }),
        Animated.delay(BLINK_MS),
        Animated.timing(opacity, {
          toValue: 1,
          duration: 0,
          useNativeDriver: false,
        }),
      ]),
    );
    loop.start();
    return () => loop.stop();
  }, [animate, opacity]);

  const text = { color: theme.accent, fontSize: dimension.text };
  return (
    <View
      testID="brand-mark"
      accessible
      accessibilityRole="header"
      accessibilityLabel="Juniper"
      style={styles.row}
    >
      <Image
        testID="brand-logo"
        source={logo}
        style={{ width: dimension.logo, height: dimension.logo }}
        resizeMode="contain"
      />
      <View style={styles.name}>
        <Text style={[styles.text, text]}>Juniper</Text>
        <Animated.Text
          testID="brand-cursor"
          style={[styles.text, text, { opacity }]}
        >
          _
        </Animated.Text>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  row: { flexDirection: 'row', alignItems: 'center', gap: spacing.md },
  name: { flexDirection: 'row', alignItems: 'flex-end' },
  text: { fontWeight: '700', letterSpacing: 1.5 },
});
