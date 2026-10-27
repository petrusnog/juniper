/**
 * Copyright (c) Meta Platforms, Inc. and affiliates.
 *
 * This source code is licensed under the MIT license found in the
 * LICENSE file in the root directory of this source tree.
 *
 * @noformat
 * @oncall react_native
 * @generated SignedSource<<4adbd3ce4f1290c6b7509072a8c0afd2>>
 *
 * This file was translated from Flow by scripts/generateTypeScriptDefinitions.js
 * Original file: packages/metro/src/node-haste/lib/AssetPaths.js
 * To regenerate, run:
 *   js1 build metro-ts-defs (internal) OR
 *   yarn run build-ts-defs (OSS) 
 */

export type AssetPath = {
  assetName: string;
  name: string;
  platform: null | undefined | string;
  resolution: number;
  type: string;
};
/**
 * Return `null` if the `filePath` doesn't have a valid extension, required
 * to describe the type of an asset.
 */
export declare function tryParse(filePath: string, platforms: ReadonlySet<string>): null | undefined | AssetPath;
export declare function parse(filePath: string, platforms: ReadonlySet<string>): AssetPath;
