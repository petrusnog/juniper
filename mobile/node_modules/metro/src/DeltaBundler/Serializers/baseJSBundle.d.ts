/**
 * Copyright (c) Meta Platforms, Inc. and affiliates.
 *
 * This source code is licensed under the MIT license found in the
 * LICENSE file in the root directory of this source tree.
 *
 * @noformat
 * @oncall react_native
 * @generated SignedSource<<031bce5849e92c342ef2e74f2a84aca6>>
 *
 * This file was translated from Flow by scripts/generateTypeScriptDefinitions.js
 * Original file: packages/metro/src/DeltaBundler/Serializers/baseJSBundle.js
 * To regenerate, run:
 *   js1 build metro-ts-defs (internal) OR
 *   yarn run build-ts-defs (OSS) 
 */

import type {Module, ReadOnlyGraph, SerializerOptions} from '../types';
import type {Bundle} from 'metro-runtime/src/modules/types';

declare function baseJSBundle(entryPoint: string, preModules: ReadonlyArray<Module>, graph: ReadOnlyGraph, options: SerializerOptions): Bundle;
export default baseJSBundle;
