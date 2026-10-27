/**
 * Copyright (c) Meta Platforms, Inc. and affiliates.
 *
 * This source code is licensed under the MIT license found in the
 * LICENSE file in the root directory of this source tree.
 *
 * @noformat
 * @generated SignedSource<<7c26a64673afa295660693ad5f530335>>
 *
 * This file was translated from Flow by scripts/generateTypeScriptDefinitions.js
 * Original file: packages/metro-file-map/src/plugins/haste/computeConflicts.js
 * To regenerate, run:
 *   js1 build metro-ts-defs (internal) OR
 *   yarn run build-ts-defs (OSS) 
 */

import type {HasteMapItem} from '../../flow-types';

type Conflict = {
  id: string;
  platform: string | null;
  absolutePaths: Array<string>;
  type: 'duplicate' | 'shadowing';
};
export declare function computeHasteConflicts(
  options: Readonly<{
    duplicates: ReadonlyMap<string, ReadonlyMap<string, ReadonlyMap<string, number>>>;
    map: ReadonlyMap<string, HasteMapItem>;
    rootDir: string;
  }>,
): Array<Conflict>;
