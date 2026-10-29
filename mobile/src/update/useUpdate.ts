import { useSyncExternalStore } from 'react';
import { UpdateController, UpdateState } from './UpdateController';

export function useUpdate(controller: UpdateController): UpdateState {
  return useSyncExternalStore(controller.subscribe, controller.getState);
}
