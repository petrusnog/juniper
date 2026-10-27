/**
 * Copyright (c) Meta Platforms, Inc. and affiliates.
 *
 * This source code is licensed under the MIT license found in the
 * LICENSE file in the root directory of this source tree.
 *
 * @noformat
 * @oncall react_native
 * @generated SignedSource<<957f845142e4ded7754d7adeb2baa2f8>>
 *
 * This file was translated from Flow by scripts/generateTypeScriptDefinitions.js
 * Original file: packages/metro-file-map/src/cache/NoopCacheManager.js
 * To regenerate, run:
 *   js1 build metro-ts-defs (internal) OR
 *   yarn run build-ts-defs (OSS) 
 */

import type {CacheData, CacheManager} from '../flow-types';
/**
 * A `CacheManager` that never reads or writes.
 */
export declare class NoopCacheManager implements CacheManager {
  read(): Promise<null | undefined | CacheData>;
  write(): Promise<void>;
  end(): Promise<void>;
}
