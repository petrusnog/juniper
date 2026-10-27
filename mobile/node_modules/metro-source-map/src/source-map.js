"use strict";

Object.defineProperty(exports, "__esModule", {
  value: true,
});
Object.defineProperty(exports, "BundleBuilder", {
  enumerable: true,
  get: function () {
    return _BundleBuilder.BundleBuilder;
  },
});
Object.defineProperty(exports, "Consumer", {
  enumerable: true,
  get: function () {
    return _Consumer.default;
  },
});
Object.defineProperty(exports, "composeSourceMaps", {
  enumerable: true,
  get: function () {
    return _composeSourceMaps.default;
  },
});
Object.defineProperty(exports, "createIndexMap", {
  enumerable: true,
  get: function () {
    return _BundleBuilder.createIndexMap;
  },
});
exports.fromRawMappings = fromRawMappings;
exports.fromRawMappingsIndexed = fromRawMappingsIndexed;
exports.fromRawMappingsNonBlocking = fromRawMappingsNonBlocking;
Object.defineProperty(exports, "functionMapBabelPlugin", {
  enumerable: true,
  get: function () {
    return _generateFunctionMap.functionMapBabelPlugin;
  },
});
Object.defineProperty(exports, "generateFunctionMap", {
  enumerable: true,
  get: function () {
    return _generateFunctionMap.generateFunctionMap;
  },
});
exports.isVlqMap = isVlqMap;
Object.defineProperty(exports, "normalizeSourcePath", {
  enumerable: true,
  get: function () {
    return _normalizeSourcePath.default;
  },
});
exports.toBabelSegments = toBabelSegments;
exports.toSegmentTuple = toSegmentTuple;
exports.tuplesFromBabelDecodedMap = tuplesFromBabelDecodedMap;
exports.vlqMapFromBabelDecodedMap = vlqMapFromBabelDecodedMap;
exports.vlqMapFromTuples = vlqMapFromTuples;
var _BundleBuilder = require("./BundleBuilder");
var _composeSourceMaps = _interopRequireDefault(require("./composeSourceMaps"));
var _Consumer = _interopRequireDefault(require("./Consumer"));
var _normalizeSourcePath = _interopRequireDefault(
  require("./Consumer/normalizeSourcePath"),
);
var _generateFunctionMap = require("./generateFunctionMap");
var _Generator = _interopRequireDefault(require("./Generator"));
var _nullthrows = _interopRequireDefault(require("nullthrows"));
var _sourceMap = _interopRequireDefault(require("source-map"));
function _interopRequireDefault(e) {
  return e && e.__esModule ? e : { default: e };
}
class IndexedSourceMapResult {
  #sections;
  constructor(sections) {
    this.#sections = sections;
  }
  toMap(file, options) {
    const sections =
      options?.excludeSource === true
        ? this.#sections.map((section) => {
            const { sourcesContent: _, ...map } = section.map;
            return {
              ...section,
              map,
            };
          })
        : this.#sections;
    return (0, _BundleBuilder.createIndexMap)(file, sections);
  }
  toString(file, options) {
    return JSON.stringify(this.toMap(file, options));
  }
}
function isVlqMap(map) {
  return map != null && !Array.isArray(map) && typeof map.mappings === "string";
}
function fromRawMappingsImpl(isBlocking, onDone, modules, offsetLines) {
  const modulesToProcess = modules.slice();
  const generator = new _Generator.default();
  let carryOver = offsetLines;
  function processNextModule() {
    if (modulesToProcess.length === 0) {
      return true;
    }
    const mod = (0, _nullthrows.default)(modulesToProcess.shift());
    const { code, map } = mod;
    if (isVlqMap(map)) {
      addMappingsForFile(generator, decodeVlqMap(map), mod, carryOver);
    } else if (Array.isArray(map)) {
      addMappingsForFile(generator, map, mod, carryOver);
    } else if (map != null) {
      throw new Error(
        `Unexpected module with full source map found: ${mod.path}`,
      );
    }
    carryOver = carryOver + countLines(code);
    return false;
  }
  function workLoop() {
    const time = process.hrtime();
    while (true) {
      const isDone = processNextModule();
      if (isDone) {
        onDone(generator);
        break;
      }
      if (!isBlocking) {
        const diff = process.hrtime(time);
        const NS_IN_MS = 1000000;
        if (diff[1] > 50 * NS_IN_MS) {
          setImmediate(workLoop);
          break;
        }
      }
    }
  }
  workLoop();
}
function fromRawMappings(modules, offsetLines = 0) {
  let generator;
  fromRawMappingsImpl(
    true,
    (g) => {
      generator = g;
    },
    modules,
    offsetLines,
  );
  if (generator == null) {
    throw new Error("Expected fromRawMappingsImpl() to finish synchronously.");
  }
  return generator;
}
async function fromRawMappingsNonBlocking(modules, offsetLines = 0) {
  return new Promise((resolve) => {
    fromRawMappingsImpl(false, resolve, modules, offsetLines);
  });
}
function fromRawMappingsIndexed(modules, offsetLines = 0) {
  const sections = [];
  let carryOver = offsetLines;
  for (const mod of modules) {
    if (mod.map != null) {
      sections.push({
        offset: {
          line: carryOver,
          column: 0,
        },
        map: toIndexMapSection(mod),
      });
    }
    carryOver = carryOver + countLines(mod.code);
  }
  return new IndexedSourceMapResult(sections);
}
function toIndexMapSection(module) {
  const { map, path, source, functionMap, isIgnored } = module;
  if (isVlqMap(map)) {
    let sectionMap = {
      version: 3,
      sources: [path],
      sourcesContent: [source],
      names: [...map.names],
      mappings: map.mappings,
    };
    if (functionMap != null) {
      sectionMap = {
        ...sectionMap,
        x_facebook_sources: [[functionMap]],
      };
    }
    if (isIgnored) {
      sectionMap = {
        ...sectionMap,
        x_google_ignoreList: [0],
      };
    }
    return sectionMap;
  }
  if (Array.isArray(map)) {
    const generator = new _Generator.default();
    addMappingsForFile(generator, map, module, 0);
    return generator.toMap();
  }
  throw new Error(`Unexpected module with full source map found: ${path}`);
}
function toBabelSegments(sourceMap) {
  const rawMappings = [];
  new _sourceMap.default.SourceMapConsumer(sourceMap).eachMapping((map) => {
    rawMappings.push(
      map.originalLine == null || map.originalColumn == null
        ? {
            generated: {
              line: map.generatedLine,
              column: map.generatedColumn,
            },
            source: map.source,
            name: map.name,
          }
        : {
            generated: {
              line: map.generatedLine,
              column: map.generatedColumn,
            },
            original: {
              line: map.originalLine,
              column: map.originalColumn,
            },
            source: map.source,
            name: map.name,
          },
    );
  });
  return rawMappings;
}
function toSegmentTuple(mapping) {
  const { column, line } = mapping.generated;
  const { name, original } = mapping;
  if (original == null) {
    return [line, column];
  }
  if (typeof name !== "string") {
    return [line, column, original.line, original.column];
  }
  return [line, column, original.line, original.column, name];
}
function tuplesFromBabelDecodedMap(decodedMap) {
  const { mappings, names } = decodedMap;
  const tuples = [];
  for (let line = 0, n = mappings.length; line < n; ++line) {
    const generatedLine = line + 1;
    const segments = mappings[line];
    for (let i = 0, m = segments.length; i < m; ++i) {
      const segment = segments[i];
      switch (segment.length) {
        case 1:
          tuples.push([generatedLine, segment[0]]);
          break;
        case 4:
          tuples.push([generatedLine, segment[0], segment[2] + 1, segment[3]]);
          break;
        case 5:
          tuples.push([
            generatedLine,
            segment[0],
            segment[2] + 1,
            segment[3],
            names[segment[4]],
          ]);
          break;
      }
    }
  }
  return tuples;
}
function addMappingsForFile(generator, mappings, module, carryOver) {
  generator.startFile(module.path, module.source, module.functionMap, {
    addToIgnoreList: module.isIgnored,
  });
  for (let i = 0, n = mappings.length; i < n; ++i) {
    addMapping(generator, mappings[i], carryOver);
  }
  generator.endFile();
}
function addMapping(generator, mapping, carryOver) {
  const line = mapping[0] + carryOver;
  const column = mapping[1];
  switch (mapping.length) {
    case 2:
      generator.addSimpleMapping(line, column);
      return;
    case 4:
      generator.addSourceMapping(line, column, mapping[2], mapping[3]);
      return;
    case 5:
      generator.addNamedSourceMapping(
        line,
        column,
        mapping[2],
        mapping[3],
        mapping[4],
      );
      return;
  }
  throw new Error(`Invalid mapping: [${mapping.join(", ")}]`);
}
const newline = /\r\n?|\n|\u2028|\u2029/g;
const countLines = (string) => (string.match(newline) || []).length + 1;
function decodeVlqMap(vlqMap) {
  return toBabelSegments({
    version: 3,
    sources: [""],
    names: [...vlqMap.names],
    mappings: vlqMap.mappings,
  }).map(toSegmentTuple);
}
function vlqMapFromTuples(mappings) {
  const generator = new _Generator.default();
  generator.startFile("", "", null);
  for (const mapping of mappings) {
    addMapping(generator, mapping, 0);
  }
  generator.endFile();
  const map = generator.toMap();
  return {
    mappings: map.mappings,
    names: map.names,
  };
}
function vlqMapFromBabelDecodedMap(decodedMap, terminatingMapping) {
  const generator = new _Generator.default();
  generator.startFile("", "", null);
  const { mappings, names } = decodedMap;
  let lastGeneratedLine = -1;
  let lastGeneratedColumn = -1;
  for (let line = 0, n = mappings.length; line < n; ++line) {
    const generatedLine = line + 1;
    const segments = mappings[line];
    for (let i = 0, m = segments.length; i < m; ++i) {
      const segment = segments[i];
      const generatedColumn = segment[0];
      switch (segment.length) {
        case 1:
          generator.addSimpleMapping(generatedLine, generatedColumn);
          break;
        case 4:
          generator.addSourceMapping(
            generatedLine,
            generatedColumn,
            segment[2] + 1,
            segment[3],
          );
          break;
        case 5:
          generator.addNamedSourceMapping(
            generatedLine,
            generatedColumn,
            segment[2] + 1,
            segment[3],
            names[segment[4]],
          );
          break;
        default:
          throw new Error(`Invalid mapping: [${segment.join(", ")}]`);
      }
      lastGeneratedLine = generatedLine;
      lastGeneratedColumn = generatedColumn;
    }
  }
  if (
    lastGeneratedLine !== terminatingMapping[0] ||
    lastGeneratedColumn !== terminatingMapping[1]
  ) {
    generator.addSimpleMapping(terminatingMapping[0], terminatingMapping[1]);
  }
  generator.endFile();
  const map = generator.toMap();
  return {
    mappings: map.mappings,
    names: map.names,
  };
}
