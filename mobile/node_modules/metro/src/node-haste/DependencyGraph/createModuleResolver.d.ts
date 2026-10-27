/**
 * Copyright (c) Meta Platforms, Inc. and affiliates.
 *
 * This source code is licensed under the MIT license found in the
 * LICENSE file in the root directory of this source tree.
 *
 * @noformat
 * @oncall react_native
 * @generated SignedSource<<22fcd169fdbe5a259faef0c9685b2b90>>
 *
 * This file was translated from Flow by scripts/generateTypeScriptDefinitions.js
 * Original file: packages/metro/src/node-haste/DependencyGraph/createModuleResolver.js
 * To regenerate, run:
 *   js1 build metro-ts-defs (internal) OR
 *   yarn run build-ts-defs (OSS) 
 */

import type {PackageCache} from '../PackageCache';
import type {ConfigT} from 'metro-config';
import type {FileSystem, HasteMap} from 'metro-file-map';

import {ModuleResolver} from './ModuleResolution';

export type CreateModuleResolverOptions = Readonly<{
  config: ConfigT;
  fileSystem: FileSystem;
  hasteMap: HasteMap;
  packageCache: PackageCache;
}>;
declare function createModuleResolver($$PARAM_0$$: CreateModuleResolverOptions): ModuleResolver;
export default createModuleResolver;
