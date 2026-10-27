/**
 * Copyright (c) Meta Platforms, Inc. and affiliates.
 *
 * This source code is licensed under the MIT license found in the
 * LICENSE file in the root directory of this source tree.
 *
 * @generated SignedSource<<f51e81e4f223304a89189c14cc1f28ee>>
 *
 * This file was translated from Flow by scripts/js-api/build-types/index.js.
 * Original file: packages/react-native/Libraries/LogBox/Data/parseLogBoxLog.js
 */

import type { ExceptionData } from "../../Core/NativeExceptionsManager";
import type { LogBoxLogData } from "./LogBoxLog";
import type { Stack } from "./LogBoxSymbolication";
export type ExtendedExceptionData = ExceptionData & {
  isComponentError: boolean;
};
export type Category = string;
export type CodeFrame = Readonly<{
  content: string;
  location: {
    row: number;
    column: number;
  } | undefined;
  fileName: string;
  collapse?: boolean | undefined;
}>;
export type Message = Readonly<{
  content: string;
  substitutions: ReadonlyArray<Readonly<{
    length: number;
    offset: number;
  }>>;
}>;
export declare function parseInterpolation(args: ReadonlyArray<unknown>): Readonly<{
  category: Category;
  message: Message;
}>;
export declare function parseComponentStack(message: string): {
  stack: Stack;
};
export declare function parseLogBoxException(error: ExtendedExceptionData): LogBoxLogData;
export declare function withoutANSIColorStyles(message: unknown): unknown;
export declare function parseLogBoxLog(args: ReadonlyArray<unknown>): {
  componentStack: Stack;
  category: Category;
  message: Message;
};
