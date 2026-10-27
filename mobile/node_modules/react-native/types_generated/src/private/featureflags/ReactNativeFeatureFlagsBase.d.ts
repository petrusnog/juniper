/**
 * Copyright (c) Meta Platforms, Inc. and affiliates.
 *
 * This source code is licensed under the MIT license found in the
 * LICENSE file in the root directory of this source tree.
 *
 * @generated SignedSource<<3f726257f68b8a5e94033b55e216f5f1>>
 *
 * This file was translated from Flow by scripts/js-api/build-types/index.js.
 * Original file: packages/react-native/src/private/featureflags/ReactNativeFeatureFlagsBase.js
 */

import type { ReactNativeFeatureFlagsJsOnly, ReactNativeFeatureFlagsJsOnlyOverrides } from "./ReactNativeFeatureFlags";
import NativeReactNativeFeatureFlags from "./specs/NativeReactNativeFeatureFlags";
export type Getter<T> = () => T;
export type OverridesFor<T extends {
  readonly [$$Key$$: string]: () => unknown;
}> = Partial<{ [key in keyof T]: Getter<null | undefined | ReturnType<T[key]>> }>;
export declare function createJavaScriptFlagGetter<K extends keyof ReactNativeFeatureFlagsJsOnly>(configName: K, defaultValue: ReturnType<ReactNativeFeatureFlagsJsOnly[K]>): Getter<ReturnType<ReactNativeFeatureFlagsJsOnly[K]>>;
type NativeFeatureFlags = NonNullable<typeof NativeReactNativeFeatureFlags>;
export declare function createNativeFlagGetter<K extends keyof NativeFeatureFlags>(configName: K, defaultValue: ReturnType<NonNullable<NativeFeatureFlags[K]>>, skipUnavailableNativeModuleError?: boolean): Getter<ReturnType<NonNullable<NativeFeatureFlags[K]>>>;
export declare function getOverrides(): null | undefined | ReactNativeFeatureFlagsJsOnlyOverrides;
export declare function setOverrides(newOverrides: ReactNativeFeatureFlagsJsOnlyOverrides): void;
export declare function dangerouslyResetForTesting(): void;
