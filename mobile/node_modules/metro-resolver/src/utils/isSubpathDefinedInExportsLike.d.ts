/**
 * Copyright (c) Meta Platforms, Inc. and affiliates.
 *
 * This source code is licensed under the MIT license found in the
 * LICENSE file in the root directory of this source tree.
 *
 * @noformat
 * @oncall react_native
 * @generated SignedSource<<81f42fb3ff062f769288a6ff47a47b13>>
 *
 * This file was translated from Flow by scripts/generateTypeScriptDefinitions.js
 * Original file: packages/metro-resolver/src/utils/isSubpathDefinedInExportsLike.js
 * To regenerate, run:
 *   js1 build metro-ts-defs (internal) OR
 *   yarn run build-ts-defs (OSS) 
 */

/**
 * Identifies whether the given subpath is defined in the given "exports"-like
 * mapping. Does not reduce exports conditions (therefore does not identify
 * whether the subpath is mapped to a value).
 */
import type {NormalizedExportsLikeMap} from '../types';

export declare function isSubpathDefinedInExportsLike(exportsLikeMap: NormalizedExportsLikeMap, subpath: string): boolean;
