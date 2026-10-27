/**
 * Copyright (c) Meta Platforms, Inc. and affiliates.
 *
 * This source code is licensed under the MIT license found in the
 * LICENSE file in the root directory of this source tree.
 *
 * @generated SignedSource<<367ded3d83686497acd3e6e5af39e280>>
 *
 * This file was translated from Flow by scripts/js-api/build-types/index.js.
 * Original file: packages/react-native/src/private/specs_DEPRECATED/modules/NativeExceptionsManager.js
 */

import type { TurboModule } from "../../../../Libraries/TurboModule/RCTExport";
export type StackFrame = {
  column: number | undefined;
  file: string | undefined;
  lineNumber: number | undefined;
  methodName: string;
  collapse?: boolean | undefined;
};
export type ExceptionData = {
  message: string;
  originalMessage: string | undefined;
  name: string | undefined;
  componentStack: string | undefined;
  stack: Array<StackFrame>;
  id: number;
  isFatal: boolean;
  extraData?: Object | undefined;
};
export interface Spec extends TurboModule {
  readonly reportFatalException: (message: string, stack: Array<StackFrame>, exceptionId: number) => void;
  readonly reportSoftException: (message: string, stack: Array<StackFrame>, exceptionId: number) => void;
  readonly reportException?: ((data: ExceptionData) => void) | undefined;
  readonly dismissRedbox?: (() => void) | undefined;
}
declare const ExceptionsManager: {
  reportFatalException(message: string, stack: Array<StackFrame>, exceptionId: number): void;
  reportSoftException(message: string, stack: Array<StackFrame>, exceptionId: number): void;
  dismissRedbox(): void;
  reportException(data: ExceptionData): void;
};
declare const $$NativeExceptionsManager: typeof ExceptionsManager;
declare type $$NativeExceptionsManager = typeof $$NativeExceptionsManager;
export default $$NativeExceptionsManager;
