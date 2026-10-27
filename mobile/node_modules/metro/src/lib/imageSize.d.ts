/**
 * Copyright (c) Meta Platforms, Inc. and affiliates.
 *
 * This source code is licensed under the MIT license found in the
 * LICENSE file in the root directory of this source tree.
 *
 * @noformat
 * @oncall react_native
 *
 * @lint-ignore-every LICENSELINT
 * This file retains the MIT notice required for the derived parsers below.
 * @generated SignedSource<<f83d446559d42ac7e21e470e97ff2509>>
 *
 * This file was translated from Flow by scripts/generateTypeScriptDefinitions.js
 * Original file: packages/metro/src/lib/imageSize.js
 * To regenerate, run:
 *   js1 build metro-ts-defs (internal) OR
 *   yarn run build-ts-defs (OSS) 
 */

/**
 * Image dimension parsing is derived from image-size's format support, reduced
 * to the formats Metro treats as images. See the third-party notice below.
 */

export type Dimensions = {readonly width: number; readonly height: number};
export declare function getImageDimensions(type: string, content: Buffer, filePath: string): Dimensions;
