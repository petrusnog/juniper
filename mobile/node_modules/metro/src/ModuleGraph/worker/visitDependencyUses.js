"use strict";

Object.defineProperty(exports, "__esModule", {
  value: true,
});
exports.default = visitDependencyUses;
var _traverseFile = _interopRequireDefault(require("./traverseFile"));
var _invariant = _interopRequireDefault(require("invariant"));
var _nullthrows = _interopRequireDefault(require("nullthrows"));
function _interopRequireDefault(e) {
  return e && e.__esModule ? e : { default: e };
}
function visitDependencyUses(file, visitors) {
  const {
    dependencyFilter,
    visitNonAnalyzableRequire = (_path) => {},
    visitCall = (_path, _dep) => {},
    visitOther = (_path, _dep) => {},
  } = visitors;
  if (
    dependencyFilter != null &&
    !file.dependencies.some((dep) => dependencyFilter(dep))
  ) {
    return;
  }
  const {
    requireParamBinding,
    importDefaultParamBinding,
    importAllParamBinding,
    depMapParamBinding,
  } = bindModuleIRElements(file);
  for (const path of requireParamBinding.referencePaths.concat(
    importDefaultParamBinding.referencePaths,
  )) {
    const req = bindRequireCallElements(path, {
      depMapParamBinding,
    });
    if (!req) {
      visitNonAnalyzableRequire(path);
      continue;
    }
    const dep = file.dependencies[req.depIndex];
    if (dependencyFilter != null && !dependencyFilter(dep)) {
      continue;
    }
    for (const { path: referencePath } of walkReferences(req.exprPath)) {
      if (
        referencePath.parentPath &&
        referencePath.parentPath.node.type === "CallExpression" &&
        referencePath.parentPath.node.callee === referencePath.node
      ) {
        visitCall(referencePath.parentPath, dep);
      } else {
        visitOther(referencePath, dep);
      }
    }
  }
  for (const path of importAllParamBinding.referencePaths) {
    const req = bindRequireCallElements(path, {
      depMapParamBinding,
    });
    if (!req) {
      visitNonAnalyzableRequire(path);
      continue;
    }
    const dep = file.dependencies[req.depIndex];
    if (dependencyFilter != null && !dependencyFilter(dep)) {
      continue;
    }
    for (const { path: referencePath } of walkReferences(req.exprPath)) {
      visitOther(referencePath, dep);
    }
  }
}
function* walkReferences(initialCandidateUse) {
  const candidateUses = new Map([
    [initialCandidateUse.node, initialCandidateUse],
  ]);
  for (const p of candidateUses.values()) {
    const parentPath = (0, _nullthrows.default)(p.parentPath);
    if (
      parentPath.node.type === "VariableDeclarator" &&
      parentPath.node.init === p.node &&
      parentPath.node.id.type === "Identifier"
    ) {
      const varIdNode = parentPath.node.id;
      const depBinding = (0, _nullthrows.default)(
        parentPath.scope.getBinding(varIdNode.name),
      );
      if (depBinding.constant) {
        for (const depRefPath of depBinding.referencePaths) {
          if (depRefPath.node === varIdNode) {
            continue;
          }
          if (candidateUses.has(depRefPath.node)) {
            continue;
          }
          candidateUses.set(depRefPath.node, depRefPath);
        }
        continue;
      }
    }
    yield {
      path: p,
    };
  }
}
function bindRequireCallElements(path, { depMapParamBinding }) {
  const requireCallPath = (0, _nullthrows.default)(path.parentPath);
  if (
    requireCallPath.node.type === "CallExpression" &&
    requireCallPath.node.callee === path.node &&
    requireCallPath.node.arguments.length > 0
  ) {
    const depMapReferencePath = requireCallPath.get("arguments.0");
    (0, _invariant.default)(
      !Array.isArray(depMapReferencePath),
      "Invalid Metro module IR",
    );
    if (!(
      depMapReferencePath.node.type === "MemberExpression" &&
      depMapReferencePath.node.computed &&
      depMapReferencePath.node.object.type === "Identifier" &&
      depMapReferencePath.scope.getBinding(
        depMapReferencePath.node.object.name,
      ) === depMapParamBinding
    )) {
      return null;
    }
  } else {
    return null;
  }
  const depIndexPath = requireCallPath.get("arguments.0.property");
  (0, _invariant.default)(
    !Array.isArray(depIndexPath),
    "Invalid Metro module IR",
  );
  const depIndex = depIndexPath.evaluate();
  (0, _invariant.default)(
    depIndex.confident && typeof depIndex.value === "number",
    "Invalid Metro Module IR",
  );
  return {
    depIndex: depIndex.value,
    exprPath: requireCallPath,
  };
}
function bindModuleIRElements(file) {
  let maybeState;
  (0, _traverseFile.default)(
    file,
    {
      Program(path) {
        (0, _invariant.default)(
          path.node.body.length === 1,
          "Invalid Metro module IR",
        );
        (0, _invariant.default)(
          path.node.body[0].type === "ExpressionStatement",
          "Invalid Metro module IR",
        );
        const moduleDefinerFnCall = path.node.body[0].expression;
        (0, _invariant.default)(
          moduleDefinerFnCall.type === "CallExpression",
          "Invalid Metro module IR",
        );
        (0, _invariant.default)(
          moduleDefinerFnCall.callee.type === "Identifier",
          "Invalid Metro module IR",
        );
        (0, _invariant.default)(
          moduleDefinerFnCall.callee.name === file.globalPrefix + "__d",
          "Invalid Metro module IR",
        );
        (0, _invariant.default)(
          moduleDefinerFnCall.arguments.length >= 1,
          "Invalid Metro module IR",
        );
        const moduleDefinerFn = moduleDefinerFnCall.arguments[0];
        (0, _invariant.default)(
          moduleDefinerFn.type === "FunctionExpression",
          "Invalid Metro module IR",
        );
        const allParamPaths = path.get("body.0.expression.arguments.0.params");
        (0, _invariant.default)(
          Array.isArray(allParamPaths),
          "Invalid Metro module IR",
        );
        (0, _invariant.default)(
          allParamPaths.length >= 2,
          "Invalid Metro module IR",
        );
        const requireParamPath = allParamPaths[1];
        (0, _invariant.default)(
          requireParamPath.node.type === "Identifier",
          "Invalid Metro module IR",
        );
        (0, _invariant.default)(
          allParamPaths.length >= 3,
          "Invalid Metro module IR",
        );
        const importDefaultParamPath = allParamPaths[2];
        (0, _invariant.default)(
          importDefaultParamPath.node.type === "Identifier",
          "Invalid Metro module IR",
        );
        (0, _invariant.default)(
          allParamPaths.length >= 4,
          "Invalid Metro module IR",
        );
        const importAllParamPath = allParamPaths[3];
        (0, _invariant.default)(
          importAllParamPath.node.type === "Identifier",
          "Invalid Metro module IR",
        );
        (0, _invariant.default)(
          allParamPaths.length >= 5,
          "Invalid Metro module IR",
        );
        const depMapParamPath = allParamPaths[allParamPaths.length - 1];
        (0, _invariant.default)(
          depMapParamPath.node.type === "Identifier",
          "Invalid Metro module IR",
        );
        maybeState = {
          requireParamPath,
          importDefaultParamPath,
          importAllParamPath,
          depMapParamPath,
        };
        path.skip();
      },
    },
    null,
  );
  const state = (0, _nullthrows.default)(
    maybeState,
    "Failed to bind Metro module IR elements",
  );
  return {
    get requireParamBinding() {
      return (0, _nullthrows.default)(
        state.requireParamPath.scope.getBinding(
          state.requireParamPath.node.name,
        ),
      );
    },
    get importDefaultParamBinding() {
      return (0, _nullthrows.default)(
        state.importDefaultParamPath.scope.getBinding(
          state.importDefaultParamPath.node.name,
        ),
      );
    },
    get importAllParamBinding() {
      return (0, _nullthrows.default)(
        state.importAllParamPath.scope.getBinding(
          state.importAllParamPath.node.name,
        ),
      );
    },
    get depMapParamBinding() {
      return state.depMapParamPath?.scope.getBinding(
        state.depMapParamPath.node.name,
      );
    },
  };
}
