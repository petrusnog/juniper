/**
 * Copyright (c) Meta Platforms, Inc. and affiliates.
 *
 * This source code is licensed under the MIT license found in the
 * LICENSE file in the root directory of this source tree.
 *
 * @noformat
 * @oncall react_native
 * @generated SignedSource<<b788aefa8c26712c342b687fa25d12e2>>
 *
 * This file was translated from Flow by scripts/generateTypeScriptDefinitions.js
 * Original file: packages/metro-transform-plugins/src/normalizePseudoGlobals.js
 * To regenerate, run:
 *   js1 build metro-ts-defs (internal) OR
 *   yarn run build-ts-defs (OSS) 
 */

import type {Node} from '@babel/types';

export type Options = {reservedNames: ReadonlyArray<string>};
declare function normalizePseudoglobals(ast: Node, options?: Options): ReadonlyArray<string>;
export default normalizePseudoglobals;
