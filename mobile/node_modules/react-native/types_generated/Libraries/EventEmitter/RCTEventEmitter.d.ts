/**
 * Copyright (c) Meta Platforms, Inc. and affiliates.
 *
 * This source code is licensed under the MIT license found in the
 * LICENSE file in the root directory of this source tree.
 *
 * @generated SignedSource<<517d623d035d234b09d25e8f39462de1>>
 *
 * This file was translated from Flow by scripts/js-api/build-types/index.js.
 * Original file: packages/react-native/Libraries/EventEmitter/RCTEventEmitter.js
 */

type EventEmitterModule = {
  receiveEvent: (rootNodeID: number, topLevelType: string, nativeEventParam: unknown) => void;
  receiveTouches: (eventTopLevelType: string, touches: Array<unknown>, changedIndices: Array<number>) => void;
};
declare const RCTEventEmitter: {
  register(eventEmitter: EventEmitterModule): void;
};
declare const $$RCTEventEmitter: typeof RCTEventEmitter;
declare type $$RCTEventEmitter = typeof $$RCTEventEmitter;
export default $$RCTEventEmitter;
