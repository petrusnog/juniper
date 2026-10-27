/**
 * Copyright (c) Meta Platforms, Inc. and affiliates.
 *
 * This source code is licensed under the MIT license found in the
 * LICENSE file in the root directory of this source tree.
 *
 * @noformat
 * @oncall react_native
 * @generated SignedSource<<4cfb77ae0fca67fed69c2985f70ff399>>
 *
 * This file was translated from Flow by scripts/generateTypeScriptDefinitions.js
 * Original file: packages/metro/src/DeltaBundler/Serializers/helpers/lazyModuleSwitch.js
 * To regenerate, run:
 *   js1 build metro-ts-defs (internal) OR
 *   yarn run build-ts-defs (OSS) 
 */

import type {BasicSourceMap, BundleBuilder} from 'metro-source-map';

export type LazyModuleSwitchEntry = Readonly<{
  moduleId: number | string;
  code: string;
  map: null | undefined | BasicSourceMap;
  sourcePath: string;
}>;
export declare function appendLazyModuleSwitch(builder: BundleBuilder, entries: ReadonlyArray<LazyModuleSwitchEntry>, options: Readonly<{globalPrefix: string}>): void;
