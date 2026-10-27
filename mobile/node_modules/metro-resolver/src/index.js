"use strict";

Object.defineProperty(exports, "__esModule", {
  value: true,
});
Object.defineProperty(exports, "FailedToResolveNameError", {
  enumerable: true,
  get: function () {
    return _FailedToResolveNameError.default;
  },
});
Object.defineProperty(exports, "FailedToResolvePathError", {
  enumerable: true,
  get: function () {
    return _FailedToResolvePathError.default;
  },
});
Object.defineProperty(exports, "FailedToResolveUnsupportedError", {
  enumerable: true,
  get: function () {
    return _FailedToResolveUnsupportedError.default;
  },
});
Object.defineProperty(exports, "InvalidPackageError", {
  enumerable: true,
  get: function () {
    return _InvalidPackageError.default;
  },
});
Object.defineProperty(exports, "formatFileCandidates", {
  enumerable: true,
  get: function () {
    return _formatFileCandidates.default;
  },
});
Object.defineProperty(exports, "resolve", {
  enumerable: true,
  get: function () {
    return _resolve.default;
  },
});
var _FailedToResolveNameError = _interopRequireDefault(
  require("./errors/FailedToResolveNameError"),
);
var _FailedToResolvePathError = _interopRequireDefault(
  require("./errors/FailedToResolvePathError"),
);
var _FailedToResolveUnsupportedError = _interopRequireDefault(
  require("./errors/FailedToResolveUnsupportedError"),
);
var _formatFileCandidates = _interopRequireDefault(
  require("./errors/formatFileCandidates"),
);
var _InvalidPackageError = _interopRequireDefault(
  require("./errors/InvalidPackageError"),
);
var _resolve = _interopRequireDefault(require("./resolve"));
function _interopRequireDefault(e) {
  return e && e.__esModule ? e : { default: e };
}
