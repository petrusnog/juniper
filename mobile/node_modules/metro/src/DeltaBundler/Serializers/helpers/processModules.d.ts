/**
 * Copyright (c) Meta Platforms, Inc. and affiliates.
 *
 * This source code is licensed under the MIT license found in the
 * LICENSE file in the root directory of this source tree.
 *
 * @noformat
 * @oncall react_native
 * @generated SignedSource<<f0f0df8603daefdd059d29cd8205e182>>
 *
 * This file was translated from Flow by scripts/generateTypeScriptDefinitions.js
 * Original file: packages/metro/src/DeltaBundler/Serializers/helpers/processModules.js
 * To regenerate, run:
 *   js1 build metro-ts-defs (internal) OR
 *   yarn run build-ts-defs (OSS) 
 */

import type {Module} from '../../types';
import type {Options as WrapModuleOptions} from './js';

declare function processModules(
  modules: ReadonlyArray<Module>,
  $$PARAM_1$$: Readonly<{
    filter?: ((module: Module) => boolean) | undefined;
    createModuleId: ($$PARAM_0$$: string) => number;
    dev: boolean;
    includeAsyncPaths: boolean;
    projectRoot: string;
    serverRoot: string;
    sourceUrl: null | undefined | string;
    dependencyMapReservedName?: null | undefined | string;
    unstable_inlineDependencyMap?: boolean | undefined;
    unstable_getAsyncDependencyPath?: WrapModuleOptions['unstable_getAsyncDependencyPath'] | undefined;
  }>,
): ReadonlyArray<[Module, string]>;
export default processModules;
