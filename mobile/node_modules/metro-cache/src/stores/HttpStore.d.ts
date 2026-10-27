/**
 * Copyright (c) Meta Platforms, Inc. and affiliates.
 *
 * This source code is licensed under the MIT license found in the
 * LICENSE file in the root directory of this source tree.
 *
 * @noformat
 * @oncall react_native
 * @generated SignedSource<<18d204e99472003fc8c2e02ee58fa63d>>
 *
 * This file was translated from Flow by scripts/generateTypeScriptDefinitions.js
 * Original file: packages/metro-cache/src/stores/HttpStore.js
 * To regenerate, run:
 *   js1 build metro-ts-defs (internal) OR
 *   yarn run build-ts-defs (OSS) 
 */

import HttpError from './HttpError';
import NetworkError from './NetworkError';

export type Options = EndpointOptions | {getOptions: EndpointOptions; setOptions: EndpointOptions};
type EndpointOptions = {
  endpoint: string;
  family?: 4 | 6 | undefined;
  timeout?: number | undefined;
  key?: string | ReadonlyArray<string> | Buffer | ReadonlyArray<Buffer> | undefined;
  cert?: string | ReadonlyArray<string> | Buffer | ReadonlyArray<Buffer> | undefined;
  ca?: string | ReadonlyArray<string> | Buffer | ReadonlyArray<Buffer> | undefined;
  params?: URLSearchParams | undefined;
  headers?: {[$$Key$$: string]: string} | undefined;
  additionalSuccessStatuses?: ReadonlyArray<number> | undefined;
  /**
   * Whether to include additional debug information in error messages.
   */
  debug?: boolean | undefined;
  /**
   * Retry configuration
   */
  maxAttempts?: number | undefined;
  retryNetworkErrors?: boolean | undefined;
  retryStatuses?: ReadonlySet<number> | undefined;
  socketPath?: string | undefined;
  proxy?: string | undefined;
};
declare class HttpStore<T> {
  static HttpError: typeof HttpError;
  static NetworkError: typeof NetworkError;
  constructor(options: Options);
  get(key: Buffer): Promise<null | undefined | T>;
  set(key: Buffer, value: T): Promise<void>;
  clear(): void;
}
export default HttpStore;
