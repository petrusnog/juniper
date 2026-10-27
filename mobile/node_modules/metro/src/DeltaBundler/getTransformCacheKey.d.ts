/**
 * Copyright (c) Meta Platforms, Inc. and affiliates.
 *
 * This source code is licensed under the MIT license found in the
 * LICENSE file in the root directory of this source tree.
 *
 * @noformat
 * @oncall react_native
 * @generated SignedSource<<b7da21b3224401d12e9b2e75211ca486>>
 *
 * This file was translated from Flow by scripts/generateTypeScriptDefinitions.js
 * Original file: packages/metro/src/DeltaBundler/getTransformCacheKey.js
 * To regenerate, run:
 *   js1 build metro-ts-defs (internal) OR
 *   yarn run build-ts-defs (OSS) 
 */

import type {TransformerConfig} from './Worker';

declare function getTransformCacheKey(opts: {readonly cacheVersion: string; readonly projectRoot: string; readonly transformerConfig: TransformerConfig}): string;
export default getTransformCacheKey;
