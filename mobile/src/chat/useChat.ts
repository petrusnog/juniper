import { useSyncExternalStore } from 'react';
import { ChatController } from './ChatController';
import { ChatState } from './state';

export function useChat(controller: ChatController): ChatState {
  return useSyncExternalStore(controller.subscribe, controller.getState);
}
