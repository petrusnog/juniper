"use strict";

Object.defineProperty(exports, "__esModule", {
  value: true,
});
exports.default = traverseFile;
var _core = require("@babel/core");
var _traverse = _interopRequireDefault(require("@babel/traverse"));
function _interopRequireDefault(e) {
  return e && e.__esModule ? e : { default: e };
}
function traverseFile(file, visitors, state) {
  const { ast, sourceCode } = file;
  const babelFile = new _core.File(
    {},
    {
      ast,
      code: sourceCode,
      inputMap: null,
    },
  );
  (0, _traverse.default)(ast, visitors, babelFile.scope, state);
}
