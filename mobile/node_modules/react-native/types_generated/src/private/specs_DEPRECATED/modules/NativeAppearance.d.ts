/**
 * Copyright (c) Meta Platforms, Inc. and affiliates.
 *
 * This source code is licensed under the MIT license found in the
 * LICENSE file in the root directory of this source tree.
 *
 * @generated SignedSource<<2febbfd80bce757cf46efd86001c72cf>>
 *
 * This file was translated from Flow by scripts/js-api/build-types/index.js.
 * Original file: packages/react-native/src/private/specs_DEPRECATED/modules/NativeAppearance.js
 */

import type { TurboModule } from "../../../../Libraries/TurboModule/RCTExport";
export type ColorSchemeName = "light" | "dark";
export type ColorSchemeOverride = "light" | "dark" | "auto" | "unspecified";
export type AppearancePreferences = {
  colorScheme: ColorSchemeName;
};
export interface Spec extends TurboModule {
  readonly getColorScheme: () => ColorSchemeName;
  readonly setColorScheme: (colorScheme: ColorSchemeOverride) => void;
  readonly addListener: (eventName: string) => void;
  readonly removeListeners: (count: number) => void;
}
declare const $$NativeAppearance: null | undefined | Spec;
declare type $$NativeAppearance = typeof $$NativeAppearance;
export default $$NativeAppearance;
