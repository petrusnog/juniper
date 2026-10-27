/**
 * Copyright (c) Meta Platforms, Inc. and affiliates.
 *
 * This source code is licensed under the MIT license found in the
 * LICENSE file in the root directory of this source tree.
 *
 * @generated SignedSource<<8c7e185a7eba91cf302977a0ff82fc52>>
 *
 * This file was translated from Flow by scripts/js-api/build-types/index.js.
 * Original file: packages/react-native/src/private/webapis/dom/oldstylecollections/NodeList.js.flow
 */

import type { ArrayLike } from "../../utils/ArrayLikeUtils";
declare class NodeList<T> implements Iterable<T>, ArrayLike<T> {
  [index: number]: T;
  readonly length: number;
  item(index: number): T | null;
  entries(): Iterator<[number, T]>;
  forEach<ThisType>(callbackFn: (value: T, index: number, array: NodeList<T>) => unknown, thisArg?: ThisType): void;
  keys(): Iterator<number>;
  values(): Iterator<T>;
  [Symbol.iterator](): Iterator<T>;
}
export default NodeList;
export declare function createNodeList<T>(elements: ReadonlyArray<T>): NodeList<T>;
export declare var NodeList_public: typeof NodeList;
export declare type NodeList_public = typeof NodeList_public;
