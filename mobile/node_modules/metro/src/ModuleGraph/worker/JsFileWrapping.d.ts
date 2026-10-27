/**
 * Copyright (c) Meta Platforms, Inc. and affiliates.
 *
 * This source code is licensed under the MIT license found in the
 * LICENSE file in the root directory of this source tree.
 *
 * @noformat
 * @generated SignedSource<<fb44623215b566ec1e0b7d0c2182ef0b>>
 *
 * This file was translated from Flow by scripts/generateTypeScriptDefinitions.js
 * Original file: packages/metro/src/ModuleGraph/worker/JsFileWrapping.js
 * To regenerate, run:
 *   js1 build metro-ts-defs (internal) OR
 *   yarn run build-ts-defs (OSS) 
 */

import type {File as BabelNodeFile} from '@babel/types';

declare function wrapModule(
  fileAst: BabelNodeFile,
  importDefaultName: string,
  importAllName: string,
  dependencyMapName: string,
  globalPrefix: string,
  $$PARAM_5$$?: Readonly<{
    unstable_useStaticHermesModuleFactory?: boolean | undefined;
  }>,
): {ast: BabelNodeFile; requireName: string};
declare function wrapPolyfill(fileAst: BabelNodeFile): BabelNodeFile;
declare function jsonToCommonJS(source: string): string;
declare function wrapJson(source: string, globalPrefix: string, unstable_useStaticHermesModuleFactory?: boolean): string;
/**
 * Wraps an arbitrary JS `body` source string as a `__d(function(...) { <body> })`
 * module definition, with pure string manipulation.
 *
 * This is the lower-level primitive that `wrapJson` builds on - it is also
 * exposed for callers that need to synthesize `__d`-wrapped modules from
 * strings cheaply (e.g. codegen of synthetic segment/metadata modules).
 *
 * Factory parameter names default to the `_*Unused` names `wrapJson` emits.
 * Callers whose body references one of these slots by name (typically the
 * dependency-map parameter) must pass the corresponding option so the emitted
 * factory signature exposes that name. Callers are responsible for ensuring
 * that the body does not unintentionally shadow or redeclare parameters.
 */
declare function wrapModuleString(
  body: string,
  globalPrefix: string,
  $$PARAM_2$$?: Readonly<{
    importDefaultName?: string | undefined;
    importAllName?: string | undefined;
    dependencyMapName?: string | undefined;
    unstable_useStaticHermesModuleFactory?: boolean | undefined;
  }>,
): string;
export {wrapJson, jsonToCommonJS, wrapModule, wrapModuleString, wrapPolyfill};
