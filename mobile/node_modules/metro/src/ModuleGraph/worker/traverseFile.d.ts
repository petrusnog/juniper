/**
 * Copyright (c) Meta Platforms, Inc. and affiliates.
 *
 * This source code is licensed under the MIT license found in the
 * LICENSE file in the root directory of this source tree.
 *
 * @noformat
 * @generated SignedSource<<3e34d820d8084791cc6903fa639cb309>>
 *
 * This file was translated from Flow by scripts/generateTypeScriptDefinitions.js
 * Original file: packages/metro/src/ModuleGraph/worker/traverseFile.js
 * To regenerate, run:
 *   js1 build metro-ts-defs (internal) OR
 *   yarn run build-ts-defs (OSS) 
 */

import type {IRFile} from './visitDependencyUses';
import type {TraverseOptions} from '@babel/traverse';

declare function traverseFile<TState>(file: IRFile, visitors: TraverseOptions<TState>, state: TState): void;
export default traverseFile;
