/**
 * Copyright (c) Meta Platforms, Inc. and affiliates.
 *
 * This source code is licensed under the MIT license found in the
 * LICENSE file in the root directory of this source tree.
 *
 * @generated SignedSource<<7918a468f981d653da5ec876f3d56a80>>
 *
 * This file was translated from Flow by scripts/js-api/build-types/index.js.
 * Original file: packages/react-native/Libraries/StyleSheet/PlatformColorValueTypes.js.flow
 */

import type { ProcessedColorValue } from "./processColor";
import type { NativeColorValue } from "./StyleSheet";
/**
 * Select native platform color
 * The color must match the string that exists on the native platform
 *
 * @see https://reactnative.dev/docs/platformcolor#example
 */
export declare function PlatformColor(...names: Array<string | {
  fallback: string;
}>): NativeColorValue;
export declare function normalizeColorObject(color: NativeColorValue): null | undefined | ProcessedColorValue;
export declare function processColorObject(color: NativeColorValue): null | undefined | NativeColorValue;
