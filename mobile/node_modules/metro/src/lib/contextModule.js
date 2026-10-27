"use strict";

Object.defineProperty(exports, "__esModule", {
  value: true,
});
exports.deriveAbsolutePathFromContext = deriveAbsolutePathFromContext;
exports.fileMatchesContext = fileMatchesContext;
var _nodeCrypto = _interopRequireDefault(require("node:crypto"));
var _nodePath = _interopRequireDefault(require("node:path"));
var _nullthrows = _interopRequireDefault(require("nullthrows"));
function _interopRequireDefault(e) {
  return e && e.__esModule ? e : { default: e };
}
function toHash(value) {
  return _nodeCrypto.default.createHash("sha1").update(value).digest("hex");
}
function deriveAbsolutePathFromContext(from, context) {
  const filePath = from.endsWith(_nodePath.default.sep)
    ? from.slice(0, -1)
    : from;
  return (
    filePath +
    "?ctx=" +
    toHash(
      [
        context.mode,
        context.recursive ? "recursive" : "",
        new RegExp(context.filter.pattern, context.filter.flags).toString(),
      ]
        .filter(Boolean)
        .join(" "),
    )
  );
}
function fileMatchesContext(testPath, context) {
  const filePath = _nodePath.default.relative(
    (0, _nullthrows.default)(context.from),
    testPath,
  );
  const filter = context.filter;
  if (
    !(filePath && !filePath.startsWith("..")) ||
    (!context.recursive && filePath.includes(_nodePath.default.sep)) ||
    !filter.test("./" + filePath.replace(/\\/g, "/"))
  ) {
    return false;
  }
  return true;
}
