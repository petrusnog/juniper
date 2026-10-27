"use strict";

Object.defineProperty(exports, "__esModule", {
  value: true,
});
exports.default = getMinifier;
function getMinifier(minifierPath) {
  try {
    const mod = require(minifierPath);
    return mod.__esModule === true && "default" in mod ? mod.default : mod;
  } catch (e) {
    throw new Error(
      'A problem occurred while trying to fetch the minifier. Path: "' +
        minifierPath +
        '", error message: ' +
        e.message,
    );
  }
}
