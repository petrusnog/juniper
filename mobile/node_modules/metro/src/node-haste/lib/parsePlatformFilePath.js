"use strict";

Object.defineProperty(exports, "__esModule", {
  value: true,
});
exports.default = parsePlatformFilePath;
var _nodePath = _interopRequireDefault(require("node:path"));
function _interopRequireDefault(e) {
  return e && e.__esModule ? e : { default: e };
}
const PATH_RE = /^(.+?)(\.([^.]+))?\.([^.]+)$/;
function parsePlatformFilePath(filePath, platforms) {
  const dirPath = _nodePath.default.dirname(filePath);
  const fileName = _nodePath.default.basename(filePath);
  const match = fileName.match(PATH_RE);
  if (!match) {
    return {
      baseName: fileName,
      dirPath,
      extension: null,
      platform: null,
    };
  }
  const extension = match[4] || null;
  const platform = match[3] || null;
  if (platform == null || platforms.has(platform)) {
    return {
      baseName: match[1],
      dirPath,
      extension,
      platform,
    };
  }
  const baseName = `${match[1]}.${platform}`;
  return {
    baseName,
    dirPath,
    extension,
    platform: null,
  };
}
