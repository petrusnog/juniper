"use strict";

Object.defineProperty(exports, "__esModule", {
  value: true,
});
exports.default = getSchemeResolvers;
var _metroSchemeResolver = _interopRequireDefault(
  require("./metroSchemeResolver"),
);
function _interopRequireDefault(e) {
  return e && e.__esModule ? e : { default: e };
}
function getSchemeResolvers() {
  return {
    metro: _metroSchemeResolver.default,
  };
}
