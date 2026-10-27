"use strict";

Object.defineProperty(exports, "__esModule", {
  value: true,
});
exports.default = void 0;
const BASE64_VALUES = (() => {
  const table = new Int16Array(128).fill(-1);
  const chars =
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
  for (let i = 0; i < chars.length; i++) {
    table[chars.charCodeAt(i)] = i;
  }
  return table;
})();
const VLQ_SEMICOLON = 59;
const VLQ_COMMA = 44;
function readVlq(mappings, cursor) {
  let shift = 0;
  let value = 0;
  let continuation;
  let i = cursor[0];
  do {
    const digit = BASE64_VALUES[mappings.charCodeAt(i++)];
    continuation = digit & 32;
    value = value + ((digit & 31) << shift);
    shift = shift + 5;
  } while (continuation);
  cursor[0] = i;
  const shouldNegate = value & 1;
  value = value >>> 1;
  return shouldNegate ? -value : value;
}
function isFieldAhead(mappings, index, length) {
  return (
    index < length &&
    mappings.charCodeAt(index) !== VLQ_SEMICOLON &&
    mappings.charCodeAt(index) !== VLQ_COMMA
  );
}
class LineIndexedMappings {
  #mappings;
  #lineByteOffset;
  #srcLineAtLineStart;
  #srcColAtLineStart;
  #lineCount;
  constructor(mappings) {
    const length = mappings.length;
    let lineCount = 1;
    for (let i = 0; i < length; i++) {
      if (mappings.charCodeAt(i) === VLQ_SEMICOLON) {
        lineCount++;
      }
    }
    const lineByteOffset = new Int32Array(lineCount);
    const srcLineAtLineStart = new Int32Array(lineCount);
    const srcColAtLineStart = new Int32Array(lineCount);
    let originalLine = 1;
    let originalColumn = 0;
    let line = 0;
    const cursor = [0];
    lineByteOffset[0] = 0;
    srcLineAtLineStart[0] = originalLine;
    srcColAtLineStart[0] = originalColumn;
    while (cursor[0] < length) {
      const c = mappings.charCodeAt(cursor[0]);
      if (c === VLQ_SEMICOLON) {
        cursor[0]++;
        line++;
        lineByteOffset[line] = cursor[0];
        srcLineAtLineStart[line] = originalLine;
        srcColAtLineStart[line] = originalColumn;
        continue;
      }
      if (c === VLQ_COMMA) {
        cursor[0]++;
        continue;
      }
      readVlq(mappings, cursor);
      if (isFieldAhead(mappings, cursor[0], length)) {
        readVlq(mappings, cursor);
        originalLine = originalLine + readVlq(mappings, cursor);
        originalColumn = originalColumn + readVlq(mappings, cursor);
        if (isFieldAhead(mappings, cursor[0], length)) {
          readVlq(mappings, cursor);
        }
      }
    }
    this.#mappings = mappings;
    this.#lineByteOffset = lineByteOffset;
    this.#srcLineAtLineStart = srcLineAtLineStart;
    this.#srcColAtLineStart = srcColAtLineStart;
    this.#lineCount = lineCount;
  }
  originalPositionFor(generatedLine1Based, generatedColumn0Based) {
    if (generatedLine1Based < 1 || generatedLine1Based > this.#lineCount) {
      return null;
    }
    const lineIndex = generatedLine1Based - 1;
    const mappings = this.#mappings;
    const length = mappings.length;
    const cursor = [this.#lineByteOffset[lineIndex]];
    let originalLine = this.#srcLineAtLineStart[lineIndex];
    let originalColumn = this.#srcColAtLineStart[lineIndex];
    let generatedColumn = 0;
    let found = false;
    let foundHasSource = false;
    let foundLine = 0;
    let foundColumn = 0;
    while (cursor[0] < length) {
      const c = mappings.charCodeAt(cursor[0]);
      if (c === VLQ_SEMICOLON) {
        break;
      }
      if (c === VLQ_COMMA) {
        cursor[0]++;
        continue;
      }
      generatedColumn = generatedColumn + readVlq(mappings, cursor);
      if (generatedColumn > generatedColumn0Based) {
        break;
      }
      if (isFieldAhead(mappings, cursor[0], length)) {
        readVlq(mappings, cursor);
        originalLine = originalLine + readVlq(mappings, cursor);
        originalColumn = originalColumn + readVlq(mappings, cursor);
        if (isFieldAhead(mappings, cursor[0], length)) {
          readVlq(mappings, cursor);
        }
        found = true;
        foundHasSource = true;
        foundLine = originalLine;
        foundColumn = originalColumn;
      } else {
        found = true;
        foundHasSource = false;
      }
    }
    if (!found || !foundHasSource) {
      return null;
    }
    return {
      line1Based: foundLine,
      column0Based: foundColumn,
    };
  }
}
exports.default = LineIndexedMappings;
