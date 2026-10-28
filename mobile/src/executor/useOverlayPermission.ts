import { useCallback, useEffect, useState } from 'react';
import { AppState } from 'react-native';
import { backgroundPermission } from './MediaAutoplay';

/** Whether "display over other apps" is granted; rechecked when the app comes back to the front. */
export function useOverlayPermission(
  check: () => Promise<boolean> = backgroundPermission.granted,
): { granted: boolean | null; recheck: () => void } {
  const [granted, setGranted] = useState<boolean | null>(null);
  const recheck = useCallback(() => {
    check()
      .then(setGranted)
      .catch(() => setGranted(false));
  }, [check]);

  useEffect(() => {
    recheck();
    const subscription = AppState.addEventListener('change', state => {
      if (state === 'active') {
        recheck();
      }
    });
    return () => subscription.remove();
  }, [recheck]);

  return { granted, recheck };
}
