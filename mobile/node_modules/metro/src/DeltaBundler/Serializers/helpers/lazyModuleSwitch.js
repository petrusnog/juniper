"use strict";

Object.defineProperty(exports, "__esModule", {
  value: true,
});
exports.appendLazyModuleSwitch = appendLazyModuleSwitch;
function appendLazyModuleSwitch(builder, entries, options) {
  const modulePrefix = "_";
  builder.append(
    `var ${modulePrefix}${options.globalPrefix}__d = ${options.globalPrefix}__d;`,
  );
  builder.append("switch (moduleId) {\n");
  for (const entry of entries) {
    builder
      .append(
        "case " + entry.moduleId + ":\n" + modulePrefix,
        moduleAnchorMap(entry.sourcePath),
      )
      .append(entry.code, entry.map)
      .append("return;\n");
  }
  builder.append('default: new Error("No module found for ID " + moduleId);}');
}
function moduleAnchorMap(sourcePath) {
  return {
    version: 3,
    sources: [sourcePath],
    names: [],
    mappings: "AAAA",
  };
}
