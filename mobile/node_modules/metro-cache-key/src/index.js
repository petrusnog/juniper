"use strict";

Object.defineProperty(exports, "__esModule", {
  value: true,
});
exports.getCacheKey = getCacheKey;
var _nodeCrypto = _interopRequireDefault(require("node:crypto"));
var _nodeFs = _interopRequireDefault(require("node:fs"));
function _interopRequireDefault(e) {
  return e && e.__esModule ? e : { default: e };
}
function getCacheKey(files) {
  return files
    .reduce(
      (hash, file) =>
        hash.update("\0", "utf8").update(_nodeFs.default.readFileSync(file)),
      _nodeCrypto.default.createHash("md5"),
    )
    .digest("hex");
}
