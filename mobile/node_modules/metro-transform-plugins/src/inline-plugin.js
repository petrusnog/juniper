"use strict";

Object.defineProperty(exports, "__esModule", {
  value: true,
});
exports.default = inlinePlugin;
var _createInlinePlatformChecks = _interopRequireDefault(
  require("./utils/createInlinePlatformChecks"),
);
function _interopRequireDefault(e) {
  return e && e.__esModule ? e : { default: e };
}
const env = {
  name: "env",
};
const nodeEnv = {
  name: "NODE_ENV",
};
const processId = {
  name: "process",
};
const dev = {
  name: "__DEV__",
};
function inlinePlugin({ types: t }, options) {
  const {
    isIdentifier,
    isMemberExpression,
    isObjectExpression,
    isObjectMethod,
    isObjectProperty,
    isSpreadElement,
    isStringLiteral,
  } = t;
  const { isPlatformNode, isPlatformSelectNode } = (0,
  _createInlinePlatformChecks.default)(t, options.requireName ?? "require");
  function isGlobal(binding) {
    return !binding;
  }
  const isFlowDeclared = (binding) => t.isDeclareVariable(binding.path);
  function isGlobalOrFlowDeclared(binding) {
    return !binding || isFlowDeclared(binding);
  }
  function isWriteTarget(path) {
    let child = path.node;
    let parentPath = path.parentPath;
    while (parentPath != null) {
      const parent = parentPath.node;
      if (
        (parent.type === "AssignmentExpression" ||
          parent.type === "ForInStatement" ||
          parent.type === "ForOfStatement") &&
        parent.left === child
      ) {
        return true;
      }
      if (parent.type === "UpdateExpression" && parent.argument === child) {
        return true;
      }
      if (
        parent.type === "UnaryExpression" &&
        parent.operator === "delete" &&
        parent.argument === child
      ) {
        return true;
      }
      const nestedWriteTarget =
        parent.type === "ArrayPattern" ||
        parent.type === "ObjectPattern" ||
        (parent.type === "ObjectProperty" && parent.value === child) ||
        (parent.type === "RestElement" && parent.argument === child) ||
        (parent.type === "AssignmentPattern" && parent.left === child);
      if (!nestedWriteTarget) {
        return false;
      }
      child = parent;
      parentPath = parentPath.parentPath;
    }
    return false;
  }
  const isProcessEnvNodeEnv = (node, scope) =>
    isIdentifier(node.property, nodeEnv) &&
    isMemberExpression(node.object) &&
    isIdentifier(node.object.property, env) &&
    isIdentifier(node.object.object, processId) &&
    isGlobal(scope.getBinding(processId.name));
  const isDev = (node, parent, scope) =>
    isIdentifier(node, dev) &&
    isGlobalOrFlowDeclared(scope.getBinding(dev.name));
  function findProperty(objectExpression, key, fallback) {
    for (let i = objectExpression.properties.length - 1; i >= 0; i--) {
      const p = objectExpression.properties[i];
      if (!isObjectProperty(p) && !isObjectMethod(p)) {
        continue;
      }
      if (
        (isIdentifier(p.key) && p.key.name === key) ||
        (isStringLiteral(p.key) && p.key.value === key)
      ) {
        if (isObjectProperty(p)) {
          return p.value;
        } else if (isObjectMethod(p)) {
          return t.toExpression(p);
        }
      }
    }
    return fallback();
  }
  function hasStaticProperties(objectExpression) {
    return objectExpression.properties.every((p) => {
      if (p.computed === true || isSpreadElement(p)) {
        return false;
      }
      if (isObjectMethod(p) && p.kind !== "method") {
        return false;
      }
      return isIdentifier(p.key) || isStringLiteral(p.key);
    });
  }
  return {
    visitor: {
      ReferencedIdentifier(path, state) {
        if (!state.opts.dev && isDev(path.node, path.parent, path.scope)) {
          path.replaceWith(t.booleanLiteral(state.opts.dev));
        }
      },
      MemberExpression(path, state) {
        const node = path.node;
        const scope = path.scope;
        const opts = state.opts;
        if (!isWriteTarget(path)) {
          if (
            opts.inlinePlatform &&
            isPlatformNode(node, scope, !!opts.isWrapped)
          ) {
            path.replaceWith(t.stringLiteral(opts.platform));
          } else if (!opts.dev && isProcessEnvNodeEnv(node, scope)) {
            path.replaceWith(
              t.stringLiteral(opts.dev ? "development" : "production"),
            );
          }
        }
      },
      CallExpression(path, state) {
        const node = path.node;
        const scope = path.scope;
        const arg = node.arguments[0];
        const opts = state.opts;
        if (
          opts.inlinePlatform &&
          isPlatformSelectNode(node, scope, !!opts.isWrapped) &&
          isObjectExpression(arg)
        ) {
          if (hasStaticProperties(arg)) {
            const fallback = () =>
              findProperty(arg, "native", () =>
                findProperty(arg, "default", () => t.identifier("undefined")),
              );
            path.replaceWith(findProperty(arg, opts.platform, fallback));
          }
        }
      },
    },
  };
}
