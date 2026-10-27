/**
 * Copyright (c) Meta Platforms, Inc. and affiliates.
 *
 * This source code is licensed under the MIT license found in the
 * LICENSE file in the root directory of this source tree.
 *
 * @generated SignedSource<<e43b927f5b243252caa390b5411f1864>>
 *
 * This file was translated from Flow by scripts/js-api/build-types/index.js.
 * Original file: packages/react-native/Libraries/Animated/components/AnimatedFlatList.js
 */

import type { AnimatedProps } from "../createAnimatedComponent";
import FlatList, { type FlatListProps } from "../../Lists/FlatList";
import * as React from "react";
declare const AnimatedFlatList: <ItemT = any>(props: Omit<AnimatedProps<FlatListProps<ItemT>>, "ref"> & {
  ref?: React.Ref<FlatList<ItemT>> | undefined;
}) => React.ReactNode;
declare const $$AnimatedFlatList: typeof AnimatedFlatList;
declare type $$AnimatedFlatList = typeof $$AnimatedFlatList;
export default $$AnimatedFlatList;
