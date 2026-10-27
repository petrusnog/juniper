"use strict";

Object.defineProperty(exports, "__esModule", {
  value: true,
});
exports.default = baseJSBundleWithLazyModuleDefinition;
var _getAppendScripts = _interopRequireDefault(
  require("../../lib/getAppendScripts"),
);
var _getSourceMapInfo = _interopRequireDefault(
  require("./helpers/getSourceMapInfo"),
);
var _js = require("./helpers/js");
var _lazyModuleSwitch = require("./helpers/lazyModuleSwitch");
var _processModules = _interopRequireDefault(
  require("./helpers/processModules"),
);
var _metroSourceMap = require("metro-source-map");
function _interopRequireDefault(e) {
  return e && e.__esModule ? e : { default: e };
}
function baseJSBundleWithLazyModuleDefinition(
  entryPoint,
  preModules,
  graph,
  options,
) {
  for (const module of graph.dependencies.values()) {
    options.createModuleId(module.path);
  }
  const excludeSource = options.excludeSource === true;
  const wrapOptions = {
    createModuleId: options.createModuleId,
    dev: options.dev,
    includeAsyncPaths: options.includeAsyncPaths,
    projectRoot: options.projectRoot,
    serverRoot: options.serverRoot,
    sourceUrl: options.sourceUrl,
    dependencyMapReservedName: options.dependencyMapReservedName,
    unstable_inlineDependencyMap: options.unstable_inlineDependencyMap,
    unstable_getAsyncDependencyPath: options.unstable_getAsyncDependencyPath,
  };
  const mapOptions = {
    excludeSource,
    shouldAddToIgnoreList: options.shouldAddToIgnoreList,
    getSourceUrl: options.getSourceUrl,
  };
  const builder = new _metroSourceMap.BundleBuilder(
    options.sourceUrl ?? "bundle.js",
  );
  const getModuleEntry = (module) => {
    const code = (0, _js.wrapModule)(module, wrapOptions);
    const info = (0, _getSourceMapInfo.default)(module, mapOptions);
    const map = (0, _metroSourceMap.fromRawMappings)([info]).toMap(undefined, {
      excludeSource,
    });
    return {
      code,
      map,
      moduleId: options.createModuleId(module.path),
      sourcePath: module.path,
    };
  };
  const appendModule = (module) => {
    const entry = getModuleEntry(module);
    builder.append(entry.code, entry.map);
  };
  if (!options.modulesOnly) {
    for (const module of preModules) {
      if ((0, _js.isJsModule)(module) && options.processModuleFilter(module)) {
        appendModule(module);
        builder.append("\n");
      }
    }
  }
  const modules = [...graph.dependencies.values()]
    .filter(_js.isJsModule)
    .filter(options.processModuleFilter)
    .sort(
      (a, b) => options.createModuleId(a.path) - options.createModuleId(b.path),
    );
  builder.append("__registerSegment(0, function defSeg0(moduleId) {");
  (0, _lazyModuleSwitch.appendLazyModuleSwitch)(
    builder,
    modules.map(getModuleEntry),
    {
      globalPrefix: options.globalPrefix,
    },
  );
  builder.append("});\n");
  const postScripts = (0, _processModules.default)(
    (0, _getAppendScripts.default)(entryPoint, [...preModules, ...modules], {
      asyncRequireModulePath: options.asyncRequireModulePath,
      createModuleId: options.createModuleId,
      getRunModuleStatement: options.getRunModuleStatement,
      globalPrefix: options.globalPrefix,
      inlineSourceMap: options.inlineSourceMap,
      runBeforeMainModule: options.runBeforeMainModule,
      runModule: options.runModule,
      shouldAddToIgnoreList: options.shouldAddToIgnoreList,
      sourceMapUrl: options.sourceMapUrl,
      sourceUrl: options.sourceUrl,
      getSourceUrl: options.getSourceUrl,
    }),
    {
      filter: options.processModuleFilter,
      ...wrapOptions,
    },
  );
  for (const [, code] of postScripts) {
    if (code.length > 0) {
      builder.append(code + "\n");
    }
  }
  return {
    code: builder.getCode(),
    map: JSON.stringify(builder.getMap()),
  };
}
