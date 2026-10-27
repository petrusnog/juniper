/**
 * Copyright (c) Meta Platforms, Inc. and affiliates.
 *
 * This source code is licensed under the MIT license found in the
 * LICENSE file in the root directory of this source tree.
 *
 * @generated SignedSource<<1028d478babdabc71e24ec4ba8a83745>>
 *
 * This file was translated from Flow by scripts/js-api/build-types/index.js.
 * Original file: packages/react-native/src/private/renderer/events/dispatchNativeEvent.js
 */

import type EventTarget from "../../webapis/dom/events/EventTarget";
/**
 * Dispatches a native event through the EventTarget-based dispatch system.
 * This handles:
 * 1. Responder negotiation (touch handling, grant/release lifecycle)
 * 2. Normal event dispatch via dispatchTrustedEvent (capture/bubble phases)
 *
 * Called from the React renderer's dispatchEvent when
 * enableNativeEventTargetEventDispatching is enabled.
 */
declare function dispatchNativeEvent(target: EventTarget, type: string, payload: {
  [$$Key$$: string]: unknown;
}): void;
export default dispatchNativeEvent;
