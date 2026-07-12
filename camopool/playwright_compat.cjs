"use strict";

const fs = require("fs");
const Module = require("module");
const path = require("path");

const originalLoader = Module._extensions[".js"];
const target = path.join("playwright", "driver", "package", "lib", "coreBundle.js");

Module._extensions[".js"] = function camopoolLoader(module, filename) {
  if (!filename.endsWith(target)) return originalLoader(module, filename);

  let source = fs.readFileSync(filename, "utf8");
  const patches = [
    [
      "deviceScaleFactor: this._options.deviceScaleFactor || 1,\n          isMobile: !!this._options.isMobile",
      "deviceScaleFactor: this._options.deviceScaleFactor || 1",
    ],
    [
      "reducedMotion,\n          forcedColors,\n          contrast",
      "reducedMotion,\n          forcedColors",
    ],
  ];

  for (const [before, after] of patches) {
    if (!source.includes(before))
      throw new Error("CamoPool compatibility patch no longer matches Playwright coreBundle.js");
    source = source.replace(before, after);
  }
  module._compile(source, filename);
};
