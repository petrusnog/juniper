/**
 * Copyright (c) Meta Platforms, Inc. and affiliates.
 *
 * This source code is licensed under the MIT license found in the
 * LICENSE file in the root directory of this source tree.
 *
 * @generated SignedSource<<7798de311e4858731ccfd93f6ba077fd>>
 *
 * This file was translated from Flow by scripts/js-api/build-types/index.js.
 * Original file: packages/react-native/Libraries/ReactNative/FabricUIManager.js
 */

import type { MeasureInWindowOnSuccessCallback, MeasureLayoutOnSuccessCallback, MeasureOnSuccessCallback } from "../../src/private/types/HostInstance";
import type { NativeElementReference } from "../../src/private/webapis/dom/nodes/specs/NativeDOM";
import type { InternalInstanceHandle, LayoutAnimationConfig, Node } from "../Renderer/shims/ReactNativeTypes";
import type { RootTag } from "../Types/RootTagTypes";
export type NodeSet = Array<Node>;
export type NodeProps = {};
export interface Spec {
  readonly createNode: (reactTag: number, viewName: string, rootTag: RootTag, props: NodeProps, instanceHandle: InternalInstanceHandle) => Node;
  readonly cloneNode: (node: Node) => Node;
  readonly cloneNodeWithNewChildren: (node: Node) => Node;
  readonly cloneNodeWithNewProps: (node: Node, newProps: NodeProps) => Node;
  readonly cloneNodeWithNewChildrenAndProps: (node: Node, newProps: NodeProps) => Node;
  readonly createChildSet: (rootTag: RootTag) => NodeSet;
  readonly appendChild: (parentNode: Node, child: Node) => Node;
  readonly appendChildToSet: (childSet: NodeSet, child: Node) => void;
  readonly completeRoot: (rootTag: RootTag, childSet: NodeSet) => void;
  readonly measure: (node: Node | NativeElementReference, callback: MeasureOnSuccessCallback) => void;
  readonly measureInWindow: (node: Node | NativeElementReference, callback: MeasureInWindowOnSuccessCallback) => void;
  readonly measureLayout: (node: Node | NativeElementReference, relativeNode: Node | NativeElementReference, onFail: () => void, onSuccess: MeasureLayoutOnSuccessCallback) => void;
  readonly configureNextLayoutAnimation: (config: LayoutAnimationConfig, callback: () => void, errorCallback: () => void) => void;
  readonly sendAccessibilityEvent: (node: Node, eventType: string) => void;
  readonly findShadowNodeByTag_DEPRECATED: (reactTag: number) => Node | undefined;
  readonly setNativeProps: (node: Node | NativeElementReference, newProps: NodeProps) => void;
  readonly dispatchCommand: (node: Node, commandName: string, args: Array<unknown>) => void;
  readonly findNodeAtPoint: (node: Node, locationX: number, locationY: number, callback: (instanceHandle: InternalInstanceHandle | undefined) => void) => void;
  readonly compareDocumentPosition: (node: Node | NativeElementReference, otherNode: Node | NativeElementReference) => number;
  readonly getBoundingClientRect: (node: Node | NativeElementReference, includeTransform: boolean) => [number, number, number, number] | undefined;
  readonly setIsJSResponder: (node: Node | NativeElementReference, isJSResponder: boolean, blockNativeResponder: boolean) => void;
  readonly unstable_DefaultEventPriority: number;
  readonly unstable_DiscreteEventPriority: number;
  readonly unstable_ContinuousEventPriority: number;
  readonly unstable_IdleEventPriority: number;
  readonly unstable_getCurrentEventPriority: () => number;
}
export declare function getFabricUIManager(): null | undefined | Spec;
