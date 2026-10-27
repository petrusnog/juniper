/**
 * Copyright (c) Meta Platforms, Inc. and affiliates.
 *
 * This source code is licensed under the MIT license found in the
 * LICENSE file in the root directory of this source tree.
 *
 * @generated SignedSource<<7c31b70db752a3429deff3323b482927>>
 *
 * This file was translated from Flow by scripts/js-api/build-types/index.js.
 * Original file: packages/react-native/Libraries/Core/ExtendedError.js
 */

export type ExtendedError = Error & ({
  jsEngine?: string | undefined;
  preventSymbolication?: boolean | undefined;
  componentStack?: string | undefined;
  isComponentError?: boolean | undefined;
  type?: string | undefined;
  cause?: {
    name: string;
    message: string;
    stackElements?: ReadonlyArray<Object> | undefined;
    stackSymbols?: ReadonlyArray<Object> | undefined;
    stackReturnAddresses?: ReadonlyArray<Object> | undefined;
  } | undefined;
});
