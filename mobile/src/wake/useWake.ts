import { useSyncExternalStore } from 'react';
import { WakeController, WakeState } from './WakeController';

export function useWake(controller: WakeController): WakeState {
  return useSyncExternalStore(controller.subscribe, controller.getState);
}
