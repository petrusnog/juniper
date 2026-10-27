"use strict";

Object.defineProperty(exports, "__esModule", {
  value: true,
});
exports.default = symbolicate;
var _search = require("metro-source-map/private/Consumer/search");
var _LineIndexedMappings = _interopRequireDefault(
  require("metro-source-map/private/LineIndexedMappings"),
);
var _Symbolication = require("metro-symbolicate/private/Symbolication");
function _interopRequireDefault(e) {
  return e && e.__esModule ? e : { default: e };
}
const lineIndexCache = new WeakMap();
function originalPositionInModule(
  map,
  generatedLine1Based,
  generatedColumn0Based,
) {
  if (Array.isArray(map)) {
    return originalPositionInTuples(
      map,
      generatedLine1Based,
      generatedColumn0Based,
    );
  }
  let decoded = lineIndexCache.get(map);
  if (decoded == null) {
    decoded = new _LineIndexedMappings.default(map.mappings);
    lineIndexCache.set(map, decoded);
  }
  return decoded.originalPositionFor(
    generatedLine1Based,
    generatedColumn0Based,
  );
}
function originalPositionInTuples(
  mappings,
  generatedLine1Based,
  generatedColumn0Based,
) {
  const target = {
    line1Based: generatedLine1Based,
    column0Based: generatedColumn0Based,
  };
  const mappingIndex = (0, _search.greatestLowerBound)(
    mappings,
    target,
    (t, candidate) => {
      if (t.line1Based === candidate[0]) {
        return t.column0Based - candidate[1];
      }
      return t.line1Based - candidate[0];
    },
  );
  if (mappingIndex == null) {
    return null;
  }
  const mapping = mappings[mappingIndex];
  if (mapping[0] !== target.line1Based || mapping.length < 4) {
    return null;
  }
  return {
    line1Based: mapping[2],
    column0Based: mapping[3],
  };
}
function createFunctionNameGetter(module) {
  const consumer = new _Symbolication.SourceMetadataMapConsumer(
    {
      version: 3,
      mappings: "",
      sources: ["dummy"],
      names: [],
      x_facebook_sources: [[module.functionMap]],
    },
    (name) => name,
  );
  return ({ line1Based, column0Based }) =>
    consumer.functionNameFor({
      line: line1Based,
      column: column0Based,
      source: "dummy",
    });
}
async function symbolicate(stack, maps, config, extraData) {
  const mapsByUrl = new Map();
  for (const [url, map] of maps) {
    mapsByUrl.set(url, map);
  }
  const functionNameGetters = new Map();
  function findModule(frame) {
    const map = mapsByUrl.get(frame.file);
    if (!map || frame.lineNumber == null) {
      return null;
    }
    const moduleIndex = (0, _search.greatestLowerBound)(
      map,
      frame.lineNumber,
      (target, candidate) => target - candidate.firstLine1Based,
    );
    if (moduleIndex == null) {
      return null;
    }
    return map[moduleIndex];
  }
  function findOriginalPos(frame, module) {
    const lineNumber = frame.lineNumber;
    const column = frame.column;
    if (module.map == null || lineNumber == null || column == null) {
      return null;
    }
    return originalPositionInModule(
      module.map,
      lineNumber - module.firstLine1Based + 1,
      column,
    );
  }
  function findFunctionName(originalPos, module) {
    if (module.functionMap) {
      let getFunctionName = functionNameGetters.get(module);
      if (!getFunctionName) {
        getFunctionName = createFunctionNameGetter(module);
        functionNameGetters.set(module, getFunctionName);
      }
      return getFunctionName(originalPos);
    }
    return null;
  }
  function symbolicateFrame(frame) {
    const module = findModule(frame);
    if (!module) {
      return {
        ...frame,
      };
    }
    const originalPos = findOriginalPos(frame, module);
    if (!originalPos) {
      return {
        ...frame,
      };
    }
    const methodName =
      findFunctionName(originalPos, module) ?? frame.methodName;
    return {
      ...frame,
      methodName,
      file: module.path,
      lineNumber: originalPos.line1Based,
      column: originalPos.column0Based,
    };
  }
  async function customizeFrame(frame) {
    const customizations =
      (await config.symbolicator.customizeFrame(frame)) || {};
    return {
      ...frame,
      ...customizations,
    };
  }
  async function customizeStack(symbolicatedStack) {
    return await config.symbolicator.customizeStack(
      symbolicatedStack,
      extraData,
    );
  }
  return Promise.all(stack.map(symbolicateFrame).map(customizeFrame)).then(
    customizeStack,
  );
}
