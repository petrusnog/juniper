"use strict";

Object.defineProperty(exports, "__esModule", {
  value: true,
});
exports.logError = logError;
exports.logInfo = logInfo;
exports.logWarning = logWarning;
exports.nullReporter = void 0;
var _nodeTty = _interopRequireDefault(require("node:tty"));
var _nodeUtil = _interopRequireDefault(require("node:util"));
function _interopRequireDefault(e) {
  return e && e.__esModule ? e : { default: e };
}
const supportsColor = () =>
  process.stdout instanceof _nodeTty.default.WriteStream &&
  process.stdout.hasColors();
function logWarning(terminal, format, ...args) {
  const str = _nodeUtil.default.format(format, ...args);
  terminal.log(
    "%s %s",
    _nodeUtil.default.styleText(["yellow", "inverse", "bold"], " WARN "),
    str,
  );
}
function logError(terminal, format, ...args) {
  terminal.log(
    "%s %s",
    _nodeUtil.default.styleText(["red", "inverse", "bold"], " ERROR "),
    _nodeUtil.default.format(
      supportsColor()
        ? format
        : _nodeUtil.default.stripVTControlCharacters(format),
      ...args,
    ),
  );
}
function logInfo(terminal, format, ...args) {
  const str = _nodeUtil.default.format(format, ...args);
  terminal.log(
    "%s %s",
    _nodeUtil.default.styleText(["cyan", "inverse", "bold"], " INFO "),
    str,
  );
}
const nullReporter = (exports.nullReporter = {
  update() {},
});
