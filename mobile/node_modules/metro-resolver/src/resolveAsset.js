"use strict";

Object.defineProperty(exports, "__esModule", {
  value: true,
});
exports.default = resolveAsset;
var _nodePath = _interopRequireDefault(require("node:path"));
function _interopRequireDefault(e) {
  return e && e.__esModule ? e : { default: e };
}
function resolveAsset(context, filePath) {
  const dirPath = _nodePath.default.dirname(filePath);
  const extension = _nodePath.default.extname(filePath);
  const basename = _nodePath.default.basename(filePath, extension);
  try {
    if (!/@\d+(?:\.\d+)?x$/.test(basename)) {
      const assets = context.resolveAsset(dirPath, basename, extension);
      if (assets != null) {
        return {
          type: "assetFiles",
          filePaths: assets,
        };
      }
    }
  } catch (e) {}
  return null;
}
