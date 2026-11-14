import { RefObject, useCallback, useEffect, useRef, useState } from 'react';
import { Dimensions, Keyboard } from 'react-native';
import type { HostInstance, KeyboardEvent } from 'react-native';

/**
 * How much of the bottom of a view the keyboard covers, in dp. `keyboardTop` is where the
 * keyboard starts on screen (`null` when it is hidden); `viewBottom` is where the view ends, both
 * in window coordinates.
 */
export function keyboardOverlap(
  viewBottom: number,
  keyboardTop: number | null,
): number {
  if (keyboardTop === null || !Number.isFinite(keyboardTop)) {
    return 0;
  }
  return Math.max(0, Math.round(viewBottom - keyboardTop));
}

/**
 * Where the keyboard starts, from what Android reports. Two readings are compared and the higher
 * keyboard wins (the one that hides more), so the message box is never left under the keyboard
 * even if one of the numbers is off (edge-to-edge screens, a custom keyboard height).
 */
export function keyboardTops(event: KeyboardEvent): number[] {
  const { screenY, height } = event.endCoordinates;
  const screenHeight = Dimensions.get('screen').height;
  return [screenY, screenHeight - height].filter(
    top => Number.isFinite(top) && top > 0,
  );
}

/** The padding that keeps the bottom of `ref`'s view above the keyboard, whatever its size. */
export function useKeyboardInset(ref: RefObject<HostInstance | null>): {
  inset: number;
  onLayout: () => void;
} {
  const [inset, setInset] = useState(0);
  const tops = useRef<number[]>([]);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const measure = useCallback(() => {
    const view = ref.current;
    if (view === null) {
      return;
    }
    if (tops.current.length === 0) {
      setInset(0);
      return;
    }
    // The real position in the window: right whether or not the system already resized it.
    view.measureInWindow((_x, y, _width, height) => {
      const bottom = y + height;
      setInset(
        Math.max(...tops.current.map(top => keyboardOverlap(bottom, top))),
      );
    });
  }, [ref]);

  useEffect(() => {
    const shown = (event: KeyboardEvent) => {
      tops.current = keyboardTops(event);
      measure();
      // The window may still be resizing: measure again once it settled.
      if (timer.current !== null) {
        clearTimeout(timer.current);
      }
      timer.current = setTimeout(measure, 150);
    };
    const hidden = () => {
      tops.current = [];
      setInset(0);
    };
    const subs = [
      Keyboard.addListener('keyboardDidShow', shown),
      Keyboard.addListener('keyboardDidChangeFrame', shown),
      Keyboard.addListener('keyboardDidHide', hidden),
    ];
    return () => {
      subs.forEach(sub => sub.remove());
      if (timer.current !== null) {
        clearTimeout(timer.current);
      }
    };
  }, [measure]);

  return { inset, onLayout: measure };
}
