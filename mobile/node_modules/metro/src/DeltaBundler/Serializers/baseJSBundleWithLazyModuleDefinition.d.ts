/**
 * Copyright (c) Meta Platforms, Inc. and affiliates.
 *
 * This source code is licensed under the MIT license found in the
 * LICENSE file in the root directory of this source tree.
 *
 * @noformat
 * @oncall react_native
 * @generated SignedSource<<8a95fdf9652a008494c67796461acb9c>>
 *
 * This file was translated from Flow by scripts/generateTypeScriptDefinitions.js
 * Original file: packages/metro/src/DeltaBundler/Serializers/baseJSBundleWithLazyModuleDefinition.js
 * To regenerate, run:
 *   js1 build metro-ts-defs (internal) OR
 *   yarn run build-ts-defs (OSS) 
 */

import type {Module, ReadOnlyGraph, SerializerOptions} from '../types';

type Options = Readonly<
  Omit<SerializerOptions, 'excludeSource'> & {
    excludeSource?: boolean | undefined;
  }
>;
/**
 * Serializes a bundle in which the graph's modules are placed inside a single
 * segment definer -- `__registerSegment(0, function (moduleId) { switch (...) })`
 * -- so that each module's `__d(...)` call runs lazily on first require instead
 * of eagerly at startup. Polyfills and the require runtime (pre-modules) and the
 * run-module calls (append scripts) are emitted eagerly, as usual.
 *
 * This is the OSS equivalent of the metro-buck "plain bundle with switch"
 * output, gated behind `serializer.unstable_lazilyDefineModules`. Unlike
 * `baseJSBundle` + `bundleToString`, which keep modules as independently
 * addressable top-level `__d(...)` statements, the switch form wraps them all in
 * one function -- so the code and source map must be assembled together (a
 * `BundleBuilder`), rather than via the flat index-map path which assumes the
 * plain layout.
 *
 * The structured graph is untouched, so deltas and HMR (which address modules
 * individually via `hmrJSBundle`) are unaffected: an HMR update is still a
 * top-level `__d(...)` that shadows the switch branch. The runtime materialises
 * a not-yet-required module on demand via its segment definer (see
 * `ensureModuleRegistered` in the require polyfill), so Fast Refresh keeps
 * working with behaviour identical to an eager bundle.
 */
declare function baseJSBundleWithLazyModuleDefinition(entryPoint: string, preModules: ReadonlyArray<Module>, graph: ReadOnlyGraph, options: Options): {code: string; map: string};
export default baseJSBundleWithLazyModuleDefinition;
