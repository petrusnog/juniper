"use strict";

Object.defineProperty(exports, "__esModule", {
  value: true,
});
exports.systemToPosixPath = exports.posixToSystemPath = void 0;
var _nodePath = _interopRequireDefault(require("node:path"));
function _interopRequireDefault(e) {
  return e && e.__esModule ? e : { default: e };
}
const systemToPosixPath = (exports.systemToPosixPath =
  _nodePath.default.sep === "/"
    ? (inputPath) => inputPath
    : (inputPath) => inputPath.replaceAll("\\", "/"));
const posixToSystemPath = (exports.posixToSystemPath =
  _nodePath.default.sep === "/"
    ? (inputPath) => inputPath
    : (inputPath) => inputPath.replaceAll("/", "\\"));
