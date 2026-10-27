/**
 * Copyright (c) Meta Platforms, Inc. and affiliates.
 *
 * This source code is licensed under the MIT license found in the
 * LICENSE file in the root directory of this source tree.
 *
 * @noformat
 * @generated SignedSource<<09b7a6330a43bf9579d4b5bf3b2acf43>>
 *
 * This file was translated from Flow by scripts/generateTypeScriptDefinitions.js
 * Original file: packages/metro/src/ModuleGraph/worker/visitDependencyUses.js
 * To regenerate, run:
 *   js1 build metro-ts-defs (internal) OR
 *   yarn run build-ts-defs (OSS) 
 */

import type {NodePath} from '@babel/traverse';
import type {CallExpression, File} from '@babel/types';
import type {TransformResultDependency as Dependency} from 'metro/private/DeltaBundler/types';

export type IRFile = Readonly<{
  ast: File;
  dependencies: ReadonlyArray<Dependency>;
  sourceCode: string;
  globalPrefix: string;
}>;
/**
 * `visitDependencyUses` is a generic utility for statically analysing /
 * meta-evaluating references to dependencies in the body of a module compiled
 * by Metro. We pass a file in "Metro IR" form, a dependency filter and a set of
 * visitors, which are invoked synchronously as appropriate.
 *
 * # Metro IR
 *
 * The form of IR that we analyse here consists of:
 *
 * * The AST after transformation (**and possibly minification**), including
 *   wrapping in a module factory and a call to `__d()`.
 * * The (unresolved) dependency list extracted by the transformer.
 * * The source code of the module (for error reporting).
 * * The `globalPrefix` config setting (so we can detect calls to the global
 *   `__d` function if there is a prefix set).
 *
 * # Visitors
 *
 * Each visitor takes a Babel AST node path and, where applicable, a Metro
 * dependency descriptor.
 *
 * A *dependency reference* is an expression that evaluates to a value imported
 * from a dependency that passes the filter. The respective dependency is
 * described by the `dep` parameter.
 *
 * * `visitCall(path, dep)`: `path` is a call expression, where the callee is a
 *   dependency reference to a default export (ESM or CommonJS).
 * * `visitOther(path, dep)`: `path` is a dependency reference that does not
 *   fall into any other analysable bucket (i.e. it's not a call). Most
 *   analysers will want to throw an error here.
 * * `visitNonAnalyzableRequire(path)`: `path` is a reference to the `require`
 *   function itself (or one of the other Metro builtins e.g. `importAll`,
 *   `importDefault`) that is used in some way that cannot be analysed - e.g.
 *   mutated or stored in a variable. Most analysers will want to throw an error
 *   here. Note that this will only be called if any dependencies pass the
 *   filter (or if there is no filter).
 */
declare function visitDependencyUses(
  file: IRFile,
  visitors: Readonly<{
    dependencyFilter?: ((dep: Dependency) => boolean) | undefined;
    visitCall: (path: NodePath<CallExpression>, dep: Dependency) => void;
    visitOther: (path: NodePath, dep: Dependency) => void;
    visitNonAnalyzableRequire: (path: NodePath) => void;
  }>,
): void;
export default visitDependencyUses;
