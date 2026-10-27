/**
 * Copyright (c) Meta Platforms, Inc. and affiliates.
 *
 * This source code is licensed under the MIT license found in the
 * LICENSE file in the root directory of this source tree.
 *
 * @noformat
 * @oncall react_native
 * @generated SignedSource<<eef0ec8b8f4d1ea61246204b6adaa3e8>>
 *
 * This file was translated from Flow by scripts/generateTypeScriptDefinitions.js
 * Original file: packages/metro/src/DeltaBundler/Serializers/helpers/getSourceMapInfo.js
 * To regenerate, run:
 *   js1 build metro-ts-defs (internal) OR
 *   yarn run build-ts-defs (OSS) 
 */

import type {Module} from '../../types';
import type {FBSourceFunctionMap, MetroSourceMapSegmentTuple, VlqMap} from 'metro-source-map';

declare function getSourceMapInfo(
  module: Module,
  options: {
    readonly excludeSource: boolean;
    readonly shouldAddToIgnoreList: ($$PARAM_0$$: Module) => boolean;
    getSourceUrl: null | undefined | ((module: Module) => string);
  },
): {
  readonly map: Array<MetroSourceMapSegmentTuple> | VlqMap;
  readonly functionMap: null | undefined | FBSourceFunctionMap;
  readonly code: string;
  readonly path: string;
  readonly source: string;
  readonly lineCount: number;
  readonly isIgnored: boolean;
};
export default getSourceMapInfo;
