/**
 * Copyright (c) Meta Platforms, Inc. and affiliates.
 *
 * This source code is licensed under the MIT license found in the
 * LICENSE file in the root directory of this source tree.
 *
 * @noformat
 * @oncall react_native
 * @generated SignedSource<<9a510c4b4eda34f59f744e0cb76b055f>>
 *
 * This file was translated from Flow by scripts/generateTypeScriptDefinitions.js
 * Original file: packages/metro-resolver/src/utils/reduceExportsLikeMap.js
 * To regenerate, run:
 *   js1 build metro-ts-defs (internal) OR
 *   yarn run build-ts-defs (OSS) 
 */

/**
 * Reduce an "exports"-like mapping to a flat subpath mapping after resolving
 * conditional exports.
 */
import type {FlattenedExportMap, NormalizedExportsLikeMap} from '../types';

export declare function reduceExportsLikeMap(exportsLikeMap: NormalizedExportsLikeMap, conditionNames: ReadonlySet<string>, createConfigError: (reason: string) => Error): FlattenedExportMap;
