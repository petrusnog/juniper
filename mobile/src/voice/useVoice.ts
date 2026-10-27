import { useSyncExternalStore } from 'react';
import { VoiceController, VoiceState } from './VoiceController';

export function useVoice(controller: VoiceController): VoiceState {
  return useSyncExternalStore(controller.subscribe, controller.getState);
}
