/**
 * Copyright (c) Meta Platforms, Inc. and affiliates.
 *
 * This source code is licensed under the MIT license found in the
 * LICENSE file in the root directory of this source tree.
 *
 * @noformat
 * @oncall react_native
 * @generated SignedSource<<012ffe7ffc0d788e91baf06c59bccb31>>
 *
 * This file was translated from Flow by scripts/generateTypeScriptDefinitions.js
 * Original file: packages/metro/src/lib/getSchemeResolvers.js
 * To regenerate, run:
 *   js1 build metro-ts-defs (internal) OR
 *   yarn run build-ts-defs (OSS) 
 */

import type {CustomResolver} from 'metro-resolver';
/**
 * The scheme resolvers Metro registers on every resolution context. Consumers
 * building their own `ResolutionContext` should spread these in to match
 * Metro's own resolution behaviour.
 *
 * These are applied beneath `config.resolver.schemeResolvers`, so a user config
 * may override any of them by reusing the same (lowercase) scheme key.
 */
declare function getSchemeResolvers(): Readonly<{
  [scheme: string]: CustomResolver;
}>;
export default getSchemeResolvers;
