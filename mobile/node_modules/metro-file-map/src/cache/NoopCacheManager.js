"use strict";

Object.defineProperty(exports, "__esModule", {
  value: true,
});
exports.NoopCacheManager = void 0;
class NoopCacheManager {
  async read() {
    return null;
  }
  async write() {}
  async end() {}
}
exports.NoopCacheManager = NoopCacheManager;
