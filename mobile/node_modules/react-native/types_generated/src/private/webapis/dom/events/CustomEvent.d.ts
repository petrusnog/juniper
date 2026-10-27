/**
 * Copyright (c) Meta Platforms, Inc. and affiliates.
 *
 * This source code is licensed under the MIT license found in the
 * LICENSE file in the root directory of this source tree.
 *
 * @generated SignedSource<<55e049246177fc2ab542bd49fdb240c0>>
 *
 * This file was translated from Flow by scripts/js-api/build-types/index.js.
 * Original file: packages/react-native/src/private/webapis/dom/events/CustomEvent.js
 */

/**
 * This module implements the `CustomEvent` interface from the DOM.
 * See https://dom.spec.whatwg.org/#interface-customevent.
 */

import type { EventInit } from "./Event";
import Event from "./Event";
export interface CustomEventInit extends EventInit {
  readonly detail?: unknown | undefined;
}
declare class CustomEvent extends Event {
  constructor(type: string, options?: null | undefined | CustomEventInit);
  get detail(): unknown;
}
export default CustomEvent;
