/**
 * Copyright (c) Meta Platforms, Inc. and affiliates.
 *
 * This source code is licensed under the MIT license found in the
 * LICENSE file in the root directory of this source tree.
 *
 * @noformat
 * @oncall react_native
 * @generated SignedSource<<b629f5e440d8b7578b742ad2df39590e>>
 *
 * This file was translated from Flow by scripts/generateTypeScriptDefinitions.js
 * Original file: packages/metro-resolver/src/utils/matchSubpathPattern.js
 * To regenerate, run:
 *   js1 build metro-ts-defs (internal) OR
 *   yarn run build-ts-defs (OSS) 
 */

/**
 * If a subpath pattern expands to the passed subpath, return the subpath match
 * (value to substitute for '*'). Otherwise, return `null`.
 *
 * See https://nodejs.org/docs/latest-v19.x/api/packages.html#subpath-patterns.
 */
export declare function matchSubpathPattern(subpathPattern: string, subpath: string): string | null;
