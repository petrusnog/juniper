/**
 * Copyright (c) Meta Platforms, Inc. and affiliates.
 *
 * This source code is licensed under the MIT license found in the
 * LICENSE file in the root directory of this source tree.
 *
 * @generated SignedSource<<254c64e9872955e09f2a80e0ebec56c5>>
 *
 * This file was translated from Flow by scripts/js-api/build-types/index.js.
 * Original file: packages/react-native/Libraries/Blob/BlobTypes.js
 */

export declare type BlobCollector = symbol & {
  __BlobCollector__: string;
};
export type BlobData = {
  blobId: string;
  offset: number;
  size: number;
  name?: string | undefined;
  type?: string | undefined;
  lastModified?: number | undefined;
};
export type BlobOptions = {
  type: string;
  lastModified: number;
};
