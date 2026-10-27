"use strict";

Object.defineProperty(exports, "__esModule", {
  value: true,
});
exports.default = void 0;
var _FileStore = _interopRequireDefault(require("./FileStore"));
var _nodeFs = _interopRequireDefault(require("node:fs"));
var _nodePath = _interopRequireDefault(require("node:path"));
function _interopRequireDefault(e) {
  return e && e.__esModule ? e : { default: e };
}
class AutoCleanFileStore extends _FileStore.default {
  #intervalMs;
  #cleanupThresholdMs;
  #root;
  constructor(opts) {
    super({
      root: opts.root,
    });
    this.#root = opts.root;
    this.#intervalMs = opts.intervalMs ?? 10 * 60 * 1000;
    this.#cleanupThresholdMs =
      opts.cleanupThresholdMs ?? 3 * 24 * 60 * 60 * 1000;
    this.#scheduleCleanup();
  }
  #scheduleCleanup() {
    setTimeout(() => this.#doCleanup(), this.#intervalMs);
  }
  #doCleanup() {
    const dirents = _nodeFs.default.readdirSync(this.#root, {
      recursive: true,
      withFileTypes: true,
    });
    let warned = false;
    const minModifiedTime = Date.now() - this.#cleanupThresholdMs;
    dirents
      .filter((dirent) => dirent.isFile())
      .forEach((dirent) => {
        const absolutePath = _nodePath.default.join(
          dirent.parentPath,
          dirent.name.toString(),
        );
        try {
          if (
            _nodeFs.default.statSync(absolutePath).mtimeMs < minModifiedTime
          ) {
            _nodeFs.default.unlinkSync(absolutePath);
          }
        } catch (e) {
          if (!warned) {
            console.warn(
              "Problem cleaning up cache for " +
                absolutePath +
                ": " +
                e.message,
            );
            warned = true;
          }
        }
      });
    this.#scheduleCleanup();
  }
}
exports.default = AutoCleanFileStore;
