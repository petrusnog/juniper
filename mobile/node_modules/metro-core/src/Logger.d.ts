/**
 * Copyright (c) Meta Platforms, Inc. and affiliates.
 *
 * This source code is licensed under the MIT license found in the
 * LICENSE file in the root directory of this source tree.
 *
 * @noformat
 * @oncall react_native
 * @generated SignedSource<<ece3dc2fc9b943982a2058f1e0a57238>>
 *
 * This file was translated from Flow by scripts/generateTypeScriptDefinitions.js
 * Original file: packages/metro-core/src/Logger.js
 * To regenerate, run:
 *   js1 build metro-ts-defs (internal) OR
 *   yarn run build-ts-defs (OSS) 
 */

import type {BundleOptions} from 'metro/private/shared/types';

export type ActionLogEntryData = {
  action_name: string;
  log_entry_label?: string | undefined;
};
export type ActionStartLogEntry = {
  action_name?: string | undefined;
  action_phase?: string | undefined;
  log_entry_label: string;
  log_session?: string | undefined;
  start_timestamp?: [number, number] | undefined;
};
export type LogEntry = {
  action_name?: string | undefined;
  action_phase?: string | undefined;
  action_result?: string | undefined;
  duration_ms?: number | undefined;
  entry_point?: string | undefined;
  file_name?: string | undefined;
  log_entry_label: string;
  log_session?: string | undefined;
  start_timestamp?: [number, number] | undefined;
  outdated_modules?: number | undefined;
  bundle_size?: number | undefined;
  bundle_options?: BundleOptions | undefined;
  bundle_hash?: string | undefined;
  build_id?: string | undefined;
  error_message?: string | undefined;
  error_stack?: string | undefined;
};
declare function on(event: string, handler: (logEntry: LogEntry) => void): void;
declare function createEntry(data: LogEntry | string): LogEntry;
declare function createActionStartEntry(data: ActionLogEntryData | string): LogEntry;
declare function createActionEndEntry(logEntry: ActionStartLogEntry, error?: null | undefined | Error): LogEntry;
declare function log(logEntry: LogEntry): LogEntry;
export {on, createEntry, createActionStartEntry, createActionEndEntry, log};
