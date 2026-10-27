/**
 * Copyright (c) Meta Platforms, Inc. and affiliates.
 *
 * This source code is licensed under the MIT license found in the
 * LICENSE file in the root directory of this source tree.
 *
 * @noformat
 * @oncall react_native
 * @generated SignedSource<<ef8e268e98b3cf3a6d1b4f0564ababa0>>
 *
 * This file was translated from Flow by scripts/generateTypeScriptDefinitions.js
 * Original file: packages/metro/src/shared/output/RamBundle.js
 * To regenerate, run:
 *   js1 build metro-ts-defs (internal) OR
 *   yarn run build-ts-defs (OSS) 
 */

import type {RamBundleInfo} from '../../DeltaBundler/Serializers/getRamBundleInfo';
import type {OutputOptions, RequestOptions} from '../types';

import Server from '../../Server';

export declare function build(packagerClient: Server, requestOptions: RequestOptions): Promise<RamBundleInfo>;
export declare function save(bundle: RamBundleInfo, options: OutputOptions, log: (x: string) => void): Promise<unknown>;
export declare const formatName: 'bundle';
export declare type formatName = typeof formatName;
