/**
 * Copyright (c) Meta Platforms, Inc. and affiliates.
 *
 * This source code is licensed under the MIT license found in the
 * LICENSE file in the root directory of this source tree.
 *
 * @generated SignedSource<<f85ebbf01090da7a9d6352ecb95f2ee4>>
 *
 * This file was translated from Flow by scripts/js-api/build-types/index.js.
 * Original file: packages/react-native/src/private/featureflags/ReactNativeFeatureFlags.js
 */

/**
 * IMPORTANT: Do NOT modify this file directly.
 *
 * To change the definition of the flags, edit
 *   packages/react-native/scripts/featureflags/ReactNativeFeatureFlags.config.js.
 *
 * To regenerate this code, run the following script from the repo root:
 *   yarn featureflags --update
 */

import { type Getter, type OverridesFor, setOverrides } from "./ReactNativeFeatureFlagsBase";
export type ReactNativeFeatureFlagsJsOnly = Readonly<{
  jsOnlyTestFlag: Getter<boolean>;
  animatedDeferStartOfTimingAnimations: Getter<boolean>;
  animatedForceNativeDriver: Getter<boolean>;
  animatedKeepListenersOnDetach: Getter<boolean>;
  animatedShouldSyncValueBeforeStartCallback: Getter<boolean>;
  deferFlatListFocusChangeRenderUpdate: Getter<boolean>;
  enableImperativeEvents_DEPRECATED: Getter<boolean>;
  enableNativeEventTargetEventDispatching: Getter<boolean>;
  externalElementInspectionEnabled: Getter<boolean>;
  fixVirtualizeListCollapseWindowSize: Getter<boolean>;
  isLayoutAnimationEnabled: Getter<boolean>;
  shouldUseAnimatedObjectForTransform: Getter<boolean>;
  shouldUseRemoveClippedSubviewsAsDefaultOnIOS: Getter<boolean>;
  shouldUseSetNativePropsInFabric: Getter<boolean>;
}>;
export type ReactNativeFeatureFlagsJsOnlyOverrides = OverridesFor<ReactNativeFeatureFlagsJsOnly>;
export type ReactNativeFeatureFlags = Readonly<Omit<ReactNativeFeatureFlagsJsOnly, "commonTestFlag" | "commonTestFlagWithoutNativeImplementation" | "cdpInteractionMetricsEnabled" | "cxxNativeAnimatedEnabled" | "defaultTextToOverflowHidden" | "disableEarlyViewCommandExecution" | "disableImageViewPreallocationAndroid" | "disableMountItemReorderingAndroid" | "disableSubviewClippingAndroid" | "disableTextLayoutManagerCacheAndroid" | "disableViewPreallocationAndroid" | "enableAccessibilityOrder" | "enableAccumulatedUpdatesInRawPropsAndroid" | "enableAndroidTextMeasurementOptimizations" | "enableBridgelessArchitecture" | "enableBufferedCallInvoker" | "enableCppPropsIteratorSetter" | "enableCustomFocusSearchOnClippedElementsAndroid" | "enableDestroyShadowTreeRevisionAsync" | "enableDoubleMeasurementFixAndroid" | "enableEagerRootViewAttachment" | "enableExclusivePropsUpdateAndroid" | "enableFabricCommitBranching" | "enableFabricLogs" | "enableFlexboxAutoMinSizeInStrictMode" | "enableFontScaleChangesUpdatingLayout" | "enableIOSCompressedTextFrameAdjustment" | "enableIOSTextBaselineOffsetPerLine" | "enableIOSViewClipToPaddingBox" | "enableImagePrefetchingAndroid" | "enableImageTransparentTintColor" | "enableImmediateUpdateModeForContentOffsetChanges" | "enableImperativeEvents" | "enableImperativeFocus" | "enableInteropViewManagerClassLookUpOptimizationIOS" | "enableIntersectionObserverByDefault" | "enableKeyEvents" | "enableLayoutAnimationsOnAndroid" | "enableLayoutAnimationsOnIOS" | "enableModuleArgumentNSNullConversionIOS" | "enableMountingCoordinatorPullModelAndroid" | "enableMutationObserverByDefault" | "enableNativeCSSParsing" | "enablePreparedTextLayout" | "enablePropsUpdateReconciliationAndroid" | "enableResizeObserverByDefault" | "enableSwiftUIBasedFilters" | "enableViewCulling" | "enableViewRecycling" | "enableViewRecyclingForImage" | "enableViewRecyclingForScrollView" | "enableViewRecyclingForText" | "enableViewRecyclingForView" | "enableVirtualViewContainerStateExperimental" | "fixDifferentiatorParentTagForUnflattenCase" | "fixMappingOfEventPrioritiesBetweenFabricAndReact" | "fixYogaFlexBasisFitContentInMainAxis" | "fuseboxAssertSingleHostState" | "fuseboxEnabledRelease" | "fuseboxFrameRecordingEnabled" | "fuseboxScreenshotCaptureEnabled" | "fuseboxWebSocketEventsEnabled" | "optimizedAnimatedPropUpdates" | "overrideBySynchronousMountPropsAtMountingAndroid" | "perfIssuesEnabled" | "perfMonitorV2Enabled" | "preparedTextCacheSize" | "preventShadowTreeCommitExhaustion" | "redBoxV2Android" | "redBoxV2IOS" | "shouldPressibilityUseW3CPointerEventsForHover" | "shouldTriggerResponderTransferOnScrollAndroid" | "skipActivityIdentityAssertionOnHostPause" | "syncAndroidClipBoundsWithOverflow" | "traceTurboModulePromiseRejectionsOnAndroid" | "updateRuntimeShadowNodeReferencesOnCommit" | "updateRuntimeShadowNodeReferencesOnCommitThread" | "useAlwaysAvailableJSErrorHandling" | "useFabricInterop" | "useNativeViewConfigsInBridgelessMode" | "useNestedScrollViewAndroid" | "useSharedAnimatedBackend" | "useTraitHiddenOnAndroid" | "useTurboModuleInterop" | "viewCullingOutsetRatio" | "viewTransitionEnabled" | "viewTransitionUseHardwareBitmapAndroid" | "virtualViewPrerenderRatio"> & {
  commonTestFlag: Getter<boolean>;
  commonTestFlagWithoutNativeImplementation: Getter<boolean>;
  cdpInteractionMetricsEnabled: Getter<boolean>;
  cxxNativeAnimatedEnabled: Getter<boolean>;
  defaultTextToOverflowHidden: Getter<boolean>;
  disableEarlyViewCommandExecution: Getter<boolean>;
  disableImageViewPreallocationAndroid: Getter<boolean>;
  disableMountItemReorderingAndroid: Getter<boolean>;
  disableSubviewClippingAndroid: Getter<boolean>;
  disableTextLayoutManagerCacheAndroid: Getter<boolean>;
  disableViewPreallocationAndroid: Getter<boolean>;
  enableAccessibilityOrder: Getter<boolean>;
  enableAccumulatedUpdatesInRawPropsAndroid: Getter<boolean>;
  enableAndroidTextMeasurementOptimizations: Getter<boolean>;
  enableBridgelessArchitecture: Getter<boolean>;
  enableBufferedCallInvoker: Getter<boolean>;
  enableCppPropsIteratorSetter: Getter<boolean>;
  enableCustomFocusSearchOnClippedElementsAndroid: Getter<boolean>;
  enableDestroyShadowTreeRevisionAsync: Getter<boolean>;
  enableDoubleMeasurementFixAndroid: Getter<boolean>;
  enableEagerRootViewAttachment: Getter<boolean>;
  enableExclusivePropsUpdateAndroid: Getter<boolean>;
  enableFabricCommitBranching: Getter<boolean>;
  enableFabricLogs: Getter<boolean>;
  enableFlexboxAutoMinSizeInStrictMode: Getter<boolean>;
  enableFontScaleChangesUpdatingLayout: Getter<boolean>;
  enableIOSCompressedTextFrameAdjustment: Getter<boolean>;
  enableIOSTextBaselineOffsetPerLine: Getter<boolean>;
  enableIOSViewClipToPaddingBox: Getter<boolean>;
  enableImagePrefetchingAndroid: Getter<boolean>;
  enableImageTransparentTintColor: Getter<boolean>;
  enableImmediateUpdateModeForContentOffsetChanges: Getter<boolean>;
  enableImperativeEvents: Getter<boolean>;
  enableImperativeFocus: Getter<boolean>;
  enableInteropViewManagerClassLookUpOptimizationIOS: Getter<boolean>;
  enableIntersectionObserverByDefault: Getter<boolean>;
  enableKeyEvents: Getter<boolean>;
  enableLayoutAnimationsOnAndroid: Getter<boolean>;
  enableLayoutAnimationsOnIOS: Getter<boolean>;
  enableModuleArgumentNSNullConversionIOS: Getter<boolean>;
  enableMountingCoordinatorPullModelAndroid: Getter<boolean>;
  enableMutationObserverByDefault: Getter<boolean>;
  enableNativeCSSParsing: Getter<boolean>;
  enablePreparedTextLayout: Getter<boolean>;
  enablePropsUpdateReconciliationAndroid: Getter<boolean>;
  enableResizeObserverByDefault: Getter<boolean>;
  enableSwiftUIBasedFilters: Getter<boolean>;
  enableViewCulling: Getter<boolean>;
  enableViewRecycling: Getter<boolean>;
  enableViewRecyclingForImage: Getter<boolean>;
  enableViewRecyclingForScrollView: Getter<boolean>;
  enableViewRecyclingForText: Getter<boolean>;
  enableViewRecyclingForView: Getter<boolean>;
  enableVirtualViewContainerStateExperimental: Getter<boolean>;
  fixDifferentiatorParentTagForUnflattenCase: Getter<boolean>;
  fixMappingOfEventPrioritiesBetweenFabricAndReact: Getter<boolean>;
  fixYogaFlexBasisFitContentInMainAxis: Getter<boolean>;
  fuseboxAssertSingleHostState: Getter<boolean>;
  fuseboxEnabledRelease: Getter<boolean>;
  fuseboxFrameRecordingEnabled: Getter<boolean>;
  fuseboxScreenshotCaptureEnabled: Getter<boolean>;
  fuseboxWebSocketEventsEnabled: Getter<boolean>;
  optimizedAnimatedPropUpdates: Getter<boolean>;
  overrideBySynchronousMountPropsAtMountingAndroid: Getter<boolean>;
  perfIssuesEnabled: Getter<boolean>;
  perfMonitorV2Enabled: Getter<boolean>;
  preparedTextCacheSize: Getter<number>;
  preventShadowTreeCommitExhaustion: Getter<boolean>;
  redBoxV2Android: Getter<boolean>;
  redBoxV2IOS: Getter<boolean>;
  shouldPressibilityUseW3CPointerEventsForHover: Getter<boolean>;
  shouldTriggerResponderTransferOnScrollAndroid: Getter<boolean>;
  skipActivityIdentityAssertionOnHostPause: Getter<boolean>;
  syncAndroidClipBoundsWithOverflow: Getter<boolean>;
  traceTurboModulePromiseRejectionsOnAndroid: Getter<boolean>;
  updateRuntimeShadowNodeReferencesOnCommit: Getter<boolean>;
  updateRuntimeShadowNodeReferencesOnCommitThread: Getter<boolean>;
  useAlwaysAvailableJSErrorHandling: Getter<boolean>;
  useFabricInterop: Getter<boolean>;
  useNativeViewConfigsInBridgelessMode: Getter<boolean>;
  useNestedScrollViewAndroid: Getter<boolean>;
  useSharedAnimatedBackend: Getter<boolean>;
  useTraitHiddenOnAndroid: Getter<boolean>;
  useTurboModuleInterop: Getter<boolean>;
  viewCullingOutsetRatio: Getter<number>;
  viewTransitionEnabled: Getter<boolean>;
  viewTransitionUseHardwareBitmapAndroid: Getter<boolean>;
  virtualViewPrerenderRatio: Getter<number>;
}>;
/**
 * JS-only flag for testing. Do NOT modify.
 */
export declare const jsOnlyTestFlag: Getter<boolean>;
export declare type jsOnlyTestFlag = typeof jsOnlyTestFlag;
/**
 * When enabled, the JS Animated layer defers the start of native-driven timing animations to the first rendered frame and re-anchors timing to prevent skipping initial frames when the UI thread is busy with layout work.
 */
export declare const animatedDeferStartOfTimingAnimations: Getter<boolean>;
export declare type animatedDeferStartOfTimingAnimations = typeof animatedDeferStartOfTimingAnimations;
/**
 * When enabled, forces `useNativeDriver` to `true` for all Animated animations and events, overriding the config (including an explicit `false`). Has no effect unless the shared animated backend is enabled, which is required to support native driver for all props.
 */
export declare const animatedForceNativeDriver: Getter<boolean>;
export declare type animatedForceNativeDriver = typeof animatedForceNativeDriver;
/**
 * When enabled, detaching an animated node from the graph retains listeners registered with `addListener` instead of removing them, so an `Animated.Value` that outlives the components using it still notifies them once it is attached again.
 */
export declare const animatedKeepListenersOnDetach: Getter<boolean>;
export declare type animatedKeepListenersOnDetach = typeof animatedKeepListenersOnDetach;
/**
 * When a useNativeDriver animation completes, syncs the JS-side AnimatedValue with the post-animation value BEFORE invoking the user-supplied start({finished}) callback. Without the flag, the callback observes the pre-animation value, which can cause downstream re-renders to read stale interpolation outputs.
 */
export declare const animatedShouldSyncValueBeforeStartCallback: Getter<boolean>;
export declare type animatedShouldSyncValueBeforeStartCallback = typeof animatedShouldSyncValueBeforeStartCallback;
/**
 * Use the deferred cell render update mechanism for focus change in FlatList.
 */
export declare const deferFlatListFocusChangeRenderUpdate: Getter<boolean>;
export declare type deferFlatListFocusChangeRenderUpdate = typeof deferFlatListFocusChangeRenderUpdate;
/**
 * Deprecated in favor of the common flag with the same name, which is the one that can be configured by OSS release stage. This is only kept so JS bundles delivered to older native builds, where the common flag is unavailable, can still enable the feature.
 */
export declare const enableImperativeEvents_DEPRECATED: Getter<boolean>;
export declare type enableImperativeEvents_DEPRECATED = typeof enableImperativeEvents_DEPRECATED;
/**
 * When enabled, the React Native renderer dispatches events through the W3C EventTarget API (addEventListener/dispatchEvent) instead of the legacy plugin-based system.
 */
export declare const enableNativeEventTargetEventDispatching: Getter<boolean>;
export declare type enableNativeEventTargetEventDispatching = typeof enableNativeEventTargetEventDispatching;
/**
 * Enable the external inspection API for DevTools to communicate with the Inspector overlay.
 */
export declare const externalElementInspectionEnabled: Getter<boolean>;
export declare type externalElementInspectionEnabled = typeof externalElementInspectionEnabled;
/**
 * Fixing an edge case where the current window size is not properly calculated with fast scrolling. Window size collapsed to 1 element even if windowSize more than the current amount of elements
 */
export declare const fixVirtualizeListCollapseWindowSize: Getter<boolean>;
export declare type fixVirtualizeListCollapseWindowSize = typeof fixVirtualizeListCollapseWindowSize;
/**
 * Function used to enable / disabled Layout Animations in React Native.
 */
export declare const isLayoutAnimationEnabled: Getter<boolean>;
export declare type isLayoutAnimationEnabled = typeof isLayoutAnimationEnabled;
/**
 * Enables use of AnimatedObject for animating transform values.
 */
export declare const shouldUseAnimatedObjectForTransform: Getter<boolean>;
export declare type shouldUseAnimatedObjectForTransform = typeof shouldUseAnimatedObjectForTransform;
/**
 * removeClippedSubviews prop will be used as the default in FlatList on iOS to match Android
 */
export declare const shouldUseRemoveClippedSubviewsAsDefaultOnIOS: Getter<boolean>;
export declare type shouldUseRemoveClippedSubviewsAsDefaultOnIOS = typeof shouldUseRemoveClippedSubviewsAsDefaultOnIOS;
/**
 * Enables use of setNativeProps in JS driven animations.
 */
export declare const shouldUseSetNativePropsInFabric: Getter<boolean>;
export declare type shouldUseSetNativePropsInFabric = typeof shouldUseSetNativePropsInFabric;
/**
 * Common flag for testing. Do NOT modify.
 */
export declare const commonTestFlag: Getter<boolean>;
export declare type commonTestFlag = typeof commonTestFlag;
/**
 * Common flag for testing (without native implementation). Do NOT modify.
 */
export declare const commonTestFlagWithoutNativeImplementation: Getter<boolean>;
export declare type commonTestFlagWithoutNativeImplementation = typeof commonTestFlagWithoutNativeImplementation;
/**
 * Enable emitting of InteractionEntry live metrics to the debugger. Requires `enableBridgelessArchitecture`.
 */
export declare const cdpInteractionMetricsEnabled: Getter<boolean>;
export declare type cdpInteractionMetricsEnabled = typeof cdpInteractionMetricsEnabled;
/**
 * Use a C++ implementation of Native Animated instead of the platform implementation.
 */
export declare const cxxNativeAnimatedEnabled: Getter<boolean>;
export declare type cxxNativeAnimatedEnabled = typeof cxxNativeAnimatedEnabled;
/**
 * When enabled, sets the default overflow style for Text components to hidden instead of visible.
 */
export declare const defaultTextToOverflowHidden: Getter<boolean>;
export declare type defaultTextToOverflowHidden = typeof defaultTextToOverflowHidden;
/**
 * Dispatch view commands in mount item order.
 */
export declare const disableEarlyViewCommandExecution: Getter<boolean>;
export declare type disableEarlyViewCommandExecution = typeof disableEarlyViewCommandExecution;
/**
 * Force disable view preallocation for images triggered from createNode off the main thread on Android
 */
export declare const disableImageViewPreallocationAndroid: Getter<boolean>;
export declare type disableImageViewPreallocationAndroid = typeof disableImageViewPreallocationAndroid;
/**
 * Prevent FabricMountingManager from reordering mountItems, which may lead to invalid state on the UI thread
 */
export declare const disableMountItemReorderingAndroid: Getter<boolean>;
export declare type disableMountItemReorderingAndroid = typeof disableMountItemReorderingAndroid;
/**
 * Force disable subview clipping for ReactViewGroup on Android
 */
export declare const disableSubviewClippingAndroid: Getter<boolean>;
export declare type disableSubviewClippingAndroid = typeof disableSubviewClippingAndroid;
/**
 * Turns off the global measurement cache used by TextLayoutManager on Android.
 */
export declare const disableTextLayoutManagerCacheAndroid: Getter<boolean>;
export declare type disableTextLayoutManagerCacheAndroid = typeof disableTextLayoutManagerCacheAndroid;
/**
 * Force disable view preallocation triggered from createNode off the main thread on Android
 */
export declare const disableViewPreallocationAndroid: Getter<boolean>;
export declare type disableViewPreallocationAndroid = typeof disableViewPreallocationAndroid;
/**
 * When enabled, the accessibilityOrder prop will propagate to native platforms and define the accessibility order.
 */
export declare const enableAccessibilityOrder: Getter<boolean>;
export declare type enableAccessibilityOrder = typeof enableAccessibilityOrder;
/**
 * When enabled, Android will accumulate updates in rawProps to reduce the number of mounting instructions for cascading re-renders.
 */
export declare const enableAccumulatedUpdatesInRawPropsAndroid: Getter<boolean>;
export declare type enableAccumulatedUpdatesInRawPropsAndroid = typeof enableAccumulatedUpdatesInRawPropsAndroid;
/**
 * Enables various optimizations throughout the path of measuring text on Android.
 */
export declare const enableAndroidTextMeasurementOptimizations: Getter<boolean>;
export declare type enableAndroidTextMeasurementOptimizations = typeof enableAndroidTextMeasurementOptimizations;
/**
 * Feature flag to enable the new bridgeless architecture.
 */
export declare const enableBridgelessArchitecture: Getter<boolean>;
export declare type enableBridgelessArchitecture = typeof enableBridgelessArchitecture;
/**
 * Route async CallInvoker work through the ReactInstance buffered runtime executor, so it is ordered against callable module calls and cannot run before the JS bundle has finished evaluating. invokeSync is unaffected.
 */
export declare const enableBufferedCallInvoker: Getter<boolean>;
export declare type enableBufferedCallInvoker = typeof enableBufferedCallInvoker;
/**
 * Enable prop iterator setter-style construction of Props in C++ (this flag is not used in Java).
 */
export declare const enableCppPropsIteratorSetter: Getter<boolean>;
export declare type enableCppPropsIteratorSetter = typeof enableCppPropsIteratorSetter;
/**
 * This enables the fabric implementation of focus search so that we can focus clipped elements
 */
export declare const enableCustomFocusSearchOnClippedElementsAndroid: Getter<boolean>;
export declare type enableCustomFocusSearchOnClippedElementsAndroid = typeof enableCustomFocusSearchOnClippedElementsAndroid;
/**
 * Enables destructor calls for ShadowTreeRevision in the background to reduce UI thread work.
 */
export declare const enableDestroyShadowTreeRevisionAsync: Getter<boolean>;
export declare type enableDestroyShadowTreeRevisionAsync = typeof enableDestroyShadowTreeRevisionAsync;
/**
 * When enabled a subset of components will avoid double measurement on Android.
 */
export declare const enableDoubleMeasurementFixAndroid: Getter<boolean>;
export declare type enableDoubleMeasurementFixAndroid = typeof enableDoubleMeasurementFixAndroid;
/**
 * Feature flag to configure eager attachment of the root view/initialisation of the JS code.
 */
export declare const enableEagerRootViewAttachment: Getter<boolean>;
export declare type enableEagerRootViewAttachment = typeof enableEagerRootViewAttachment;
/**
 * When enabled, Android will disable Props 1.5 raw value merging when Props 2.0 is available.
 */
export declare const enableExclusivePropsUpdateAndroid: Getter<boolean>;
export declare type enableExclusivePropsUpdateAndroid = typeof enableExclusivePropsUpdateAndroid;
/**
 * Enables Fabric commit branching to fix starvation problems and atomic JS updates.
 */
export declare const enableFabricCommitBranching: Getter<boolean>;
export declare type enableFabricCommitBranching = typeof enableFabricCommitBranching;
/**
 * This feature flag enables logs for Fabric.
 */
export declare const enableFabricLogs: Getter<boolean>;
export declare type enableFabricLogs = typeof enableFabricLogs;
/**
 * Enables CSS Flexbox §4.5 automatic minimum sizing under strict layout conformance. When enabled, a flex item with an undefined main-axis `min-width`/`min-height` under strict conformance receives a content-derived minimum size (per spec) instead of an undefined (0) minimum. Defaults off so the behaviour can be ramped independently of strict conformance.
 */
export declare const enableFlexboxAutoMinSizeInStrictMode: Getter<boolean>;
export declare type enableFlexboxAutoMinSizeInStrictMode = typeof enableFlexboxAutoMinSizeInStrictMode;
/**
 * Enables font scale changes updating layout for measurable nodes.
 */
export declare const enableFontScaleChangesUpdatingLayout: Getter<boolean>;
export declare type enableFontScaleChangesUpdatingLayout = typeof enableFontScaleChangesUpdatingLayout;
/**
 * Adjusts iOS Text drawing frames for compressed explicit line heights.
 */
export declare const enableIOSCompressedTextFrameAdjustment: Getter<boolean>;
export declare type enableIOSCompressedTextFrameAdjustment = typeof enableIOSCompressedTextFrameAdjustment;
/**
 * Applies base offset for each line of text separately on iOS.
 */
export declare const enableIOSTextBaselineOffsetPerLine: Getter<boolean>;
export declare type enableIOSTextBaselineOffsetPerLine = typeof enableIOSTextBaselineOffsetPerLine;
/**
 * iOS Views will clip to their padding box vs border box
 */
export declare const enableIOSViewClipToPaddingBox: Getter<boolean>;
export declare type enableIOSViewClipToPaddingBox = typeof enableIOSViewClipToPaddingBox;
/**
 * When enabled, Android will build and initiate image prefetch requests on ImageShadowNode::layout
 */
export declare const enableImagePrefetchingAndroid: Getter<boolean>;
export declare type enableImagePrefetchingAndroid = typeof enableImagePrefetchingAndroid;
/**
 * When enabled, Image `tintColor` is handled as a true optional: any defined color is applied as a tint — including `transparent` (alpha 0), which renders the image invisible — and an unset value clears a previously applied tint. When disabled, the prior behavior is preserved, where a transparent `tintColor` is treated as unassigned and the image renders untinted.
 */
export declare const enableImageTransparentTintColor: Getter<boolean>;
export declare type enableImageTransparentTintColor = typeof enableImageTransparentTintColor;
/**
 * Dispatches state updates for content offset changes synchronously on the main thread.
 */
export declare const enableImmediateUpdateModeForContentOffsetChanges: Getter<boolean>;
export declare type enableImmediateUpdateModeForContentOffsetChanges = typeof enableImmediateUpdateModeForContentOffsetChanges;
/**
 * When enabled, ReactNativeElement and ReadOnlyText expose the public EventTarget API (addEventListener, removeEventListener, dispatchEvent). When disabled, those methods are removed from those final classes.
 */
export declare const enableImperativeEvents: Getter<boolean>;
export declare type enableImperativeEvents = typeof enableImperativeEvents;
/**
 * Enable ref.focus() and ref.blur() for all views, not just TextInput.
 */
export declare const enableImperativeFocus: Getter<boolean>;
export declare type enableImperativeFocus = typeof enableImperativeFocus;
/**
 * This is to fix the issue with interop view manager where component descriptor lookup is causing ViewManager to preload.
 */
export declare const enableInteropViewManagerClassLookUpOptimizationIOS: Getter<boolean>;
export declare type enableInteropViewManagerClassLookUpOptimizationIOS = typeof enableInteropViewManagerClassLookUpOptimizationIOS;
/**
 * Enables the IntersectionObserver Web API in React Native.
 */
export declare const enableIntersectionObserverByDefault: Getter<boolean>;
export declare type enableIntersectionObserverByDefault = typeof enableIntersectionObserverByDefault;
/**
 * Enables key up/down/press events to be sent to JS from components
 */
export declare const enableKeyEvents: Getter<boolean>;
export declare type enableKeyEvents = typeof enableKeyEvents;
/**
 * When enabled, LayoutAnimations API will animate state changes on Android.
 */
export declare const enableLayoutAnimationsOnAndroid: Getter<boolean>;
export declare type enableLayoutAnimationsOnAndroid = typeof enableLayoutAnimationsOnAndroid;
/**
 * When enabled, LayoutAnimations API will animate state changes on iOS.
 */
export declare const enableLayoutAnimationsOnIOS: Getter<boolean>;
export declare type enableLayoutAnimationsOnIOS = typeof enableLayoutAnimationsOnIOS;
/**
 * Enable NSNull conversion when handling module arguments on iOS
 */
export declare const enableModuleArgumentNSNullConversionIOS: Getter<boolean>;
export declare type enableModuleArgumentNSNullConversionIOS = typeof enableModuleArgumentNSNullConversionIOS;
/**
 * When enabled, Android mounts transactions with the pull model (like iOS): the commit thread no longer pulls and builds the mount batch in schedulerShouldRenderTransactions. Instead the UI thread pulls the transaction itself via a PullTransactionMountItem enqueued in the MountItemDispatcher, builds the IntBufferBatchMountItem, and applies it synchronously. Requires `enableAccumulatedUpdatesInRawPropsAndroid` to be enabled as well, since a single pull may collapse several commits into one diff and therefore needs complete accumulated rawProps; when used together with Props 2.0, also enable `enableExclusivePropsUpdateAndroid` and `enablePropsUpdateReconciliationAndroid`.
 */
export declare const enableMountingCoordinatorPullModelAndroid: Getter<boolean>;
export declare type enableMountingCoordinatorPullModelAndroid = typeof enableMountingCoordinatorPullModelAndroid;
/**
 * Enables the MutationObserver Web API in React Native.
 */
export declare const enableMutationObserverByDefault: Getter<boolean>;
export declare type enableMutationObserverByDefault = typeof enableMutationObserverByDefault;
/**
 * Parse CSS strings using the Fabric CSS parser instead of ViewConfig processing
 */
export declare const enableNativeCSSParsing: Getter<boolean>;
export declare type enableNativeCSSParsing = typeof enableNativeCSSParsing;
/**
 * Enables caching text layout artifacts for later reuse
 */
export declare const enablePreparedTextLayout: Getter<boolean>;
export declare type enablePreparedTextLayout = typeof enablePreparedTextLayout;
/**
 * When enabled, Android will receive prop updates based on the differences between the last rendered shadow node and the last committed shadow node.
 */
export declare const enablePropsUpdateReconciliationAndroid: Getter<boolean>;
export declare type enablePropsUpdateReconciliationAndroid = typeof enablePropsUpdateReconciliationAndroid;
/**
 * Enables the ResizeObserver Web API in React Native.
 */
export declare const enableResizeObserverByDefault: Getter<boolean>;
export declare type enableResizeObserverByDefault = typeof enableResizeObserverByDefault;
/**
 * When enabled, it will use SwiftUI for filter effects like blur on iOS.
 */
export declare const enableSwiftUIBasedFilters: Getter<boolean>;
export declare type enableSwiftUIBasedFilters = typeof enableSwiftUIBasedFilters;
/**
 * Enables View Culling: as soon as a view goes off screen, it can be reused anywhere in the UI and pieced together with other items to create new UI elements.
 */
export declare const enableViewCulling: Getter<boolean>;
export declare type enableViewCulling = typeof enableViewCulling;
/**
 * Enables View Recycling. When enabled, individual ViewManagers must still opt-in.
 */
export declare const enableViewRecycling: Getter<boolean>;
export declare type enableViewRecycling = typeof enableViewRecycling;
/**
 * Enables View Recycling for <Image> via ReactViewGroup/ReactViewManager.
 */
export declare const enableViewRecyclingForImage: Getter<boolean>;
export declare type enableViewRecyclingForImage = typeof enableViewRecyclingForImage;
/**
 * Enables View Recycling for <ScrollView> via ReactViewGroup/ReactViewManager.
 */
export declare const enableViewRecyclingForScrollView: Getter<boolean>;
export declare type enableViewRecyclingForScrollView = typeof enableViewRecyclingForScrollView;
/**
 * Enables View Recycling for <Text> via ReactTextView/ReactTextViewManager.
 */
export declare const enableViewRecyclingForText: Getter<boolean>;
export declare type enableViewRecyclingForText = typeof enableViewRecyclingForText;
/**
 * Enables View Recycling for <View> via ReactViewGroup/ReactViewManager.
 */
export declare const enableViewRecyclingForView: Getter<boolean>;
export declare type enableViewRecyclingForView = typeof enableViewRecyclingForView;
/**
 * Enables the experimental version of `VirtualViewContainerState`.
 */
export declare const enableVirtualViewContainerStateExperimental: Getter<boolean>;
export declare type enableVirtualViewContainerStateExperimental = typeof enableVirtualViewContainerStateExperimental;
/**
 * Fix incorrect parentTag passed as parentTagForUpdate in the unflatten-unflatten branch of calculateShadowViewMutationsFlattener, which causes UPDATE mutations to reference a parent being created in the same batch.
 */
export declare const fixDifferentiatorParentTagForUnflattenCase: Getter<boolean>;
export declare type fixDifferentiatorParentTagForUnflattenCase = typeof fixDifferentiatorParentTagForUnflattenCase;
/**
 * Uses the default event priority instead of the discreet event priority by default when dispatching events from Fabric to React.
 */
export declare const fixMappingOfEventPrioritiesBetweenFabricAndReact: Getter<boolean>;
export declare type fixMappingOfEventPrioritiesBetweenFabricAndReact = typeof fixMappingOfEventPrioritiesBetweenFabricAndReact;
/**
 * Fix flex basis computation to not apply FitContent constraint in the main axis for non-measure container nodes, preventing unnecessary re-measurement in scroll containers.
 */
export declare const fixYogaFlexBasisFitContentInMainAxis: Getter<boolean>;
export declare type fixYogaFlexBasisFitContentInMainAxis = typeof fixYogaFlexBasisFitContentInMainAxis;
/**
 * Enable system assertion validating that Fusebox is configured with a single host. When set, the CDP backend will dynamically disable features (Perf and Network) in the event that multiple hosts are registered (undefined behaviour), and broadcast this over `ReactNativeApplication.systemStateChanged`.
 */
export declare const fuseboxAssertSingleHostState: Getter<boolean>;
export declare type fuseboxAssertSingleHostState = typeof fuseboxAssertSingleHostState;
/**
 * Flag determining if the React Native DevTools (Fusebox) CDP backend should be enabled in release builds. This flag is global and should not be changed across React Host lifetimes.
 */
export declare const fuseboxEnabledRelease: Getter<boolean>;
export declare type fuseboxEnabledRelease = typeof fuseboxEnabledRelease;
/**
 * Enable frame timings and screenshots support in the React Native DevTools CDP backend. This flag is global and should not be changed across React Host lifetimes.
 */
export declare const fuseboxFrameRecordingEnabled: Getter<boolean>;
export declare type fuseboxFrameRecordingEnabled = typeof fuseboxFrameRecordingEnabled;
/**
 * Enable Page.captureScreenshot CDP method support in the React Native DevTools CDP backend. This flag is global and should not be changed across React Host lifetimes.
 */
export declare const fuseboxScreenshotCaptureEnabled: Getter<boolean>;
export declare type fuseboxScreenshotCaptureEnabled = typeof fuseboxScreenshotCaptureEnabled;
/**
 * Enable reporting of WebSocket network events (`Network.webSocket*` CDP events) to the React Native DevTools CDP backend.
 */
export declare const fuseboxWebSocketEventsEnabled: Getter<boolean>;
export declare type fuseboxWebSocketEventsEnabled = typeof fuseboxWebSocketEventsEnabled;
/**
 * When enabled, uses optimized platform-specific paths to apply animated props synchronously. On Android, this uses a batched int/double buffer protocol with a single JNI call. On iOS, this passes AnimatedProps directly through the delegate chain and applies them via cloneProps, avoiding the folly::dynamic round-trip.
 */
export declare const optimizedAnimatedPropUpdates: Getter<boolean>;
export declare type optimizedAnimatedPropUpdates = typeof optimizedAnimatedPropUpdates;
/**
 * Override props at mounting with synchronously mounted (i.e. direct manipulation) props from Native Animated.
 */
export declare const overrideBySynchronousMountPropsAtMountingAndroid: Getter<boolean>;
export declare type overrideBySynchronousMountPropsAtMountingAndroid = typeof overrideBySynchronousMountPropsAtMountingAndroid;
/**
 * Enable reporting Performance Issues (`detail.devtools.performanceIssue`). Displayed in the V2 Performance Monitor and the "Performance Issues" sub-panel in DevTools.
 */
export declare const perfIssuesEnabled: Getter<boolean>;
export declare type perfIssuesEnabled = typeof perfIssuesEnabled;
/**
 * Enable the V2 in-app Performance Monitor. This flag is global and should not be changed across React Host lifetimes.
 */
export declare const perfMonitorV2Enabled: Getter<boolean>;
export declare type perfMonitorV2Enabled = typeof perfMonitorV2Enabled;
/**
 * Number cached PreparedLayouts in TextLayoutManager cache
 */
export declare const preparedTextCacheSize: Getter<number>;
export declare type preparedTextCacheSize = typeof preparedTextCacheSize;
/**
 * Enables a new mechanism in ShadowTree to prevent problems caused by multiple threads trying to commit concurrently. If a thread tries to commit a few times unsuccessfully, it will acquire a lock and try again.
 */
export declare const preventShadowTreeCommitExhaustion: Getter<boolean>;
export declare type preventShadowTreeCommitExhaustion = typeof preventShadowTreeCommitExhaustion;
/**
 * Use the redesigned RedBox error overlay on Android, styled to match the LogBox visual language.
 */
export declare const redBoxV2Android: Getter<boolean>;
export declare type redBoxV2Android = typeof redBoxV2Android;
/**
 * Use the redesigned RedBox error overlay on iOS, styled to match the LogBox visual language.
 */
export declare const redBoxV2IOS: Getter<boolean>;
export declare type redBoxV2IOS = typeof redBoxV2IOS;
/**
 * Function used to enable / disable Pressibility from using W3C Pointer Events for its hover callbacks
 */
export declare const shouldPressibilityUseW3CPointerEventsForHover: Getter<boolean>;
export declare type shouldPressibilityUseW3CPointerEventsForHover = typeof shouldPressibilityUseW3CPointerEventsForHover;
/**
 * Do not emit touchcancel from Android ScrollView, instead native topScroll event will trigger responder transfer and terminate in RN renderer.
 */
export declare const shouldTriggerResponderTransferOnScrollAndroid: Getter<boolean>;
export declare type shouldTriggerResponderTransferOnScrollAndroid = typeof shouldTriggerResponderTransferOnScrollAndroid;
/**
 * Skip activity identity assertion in ReactHostImpl::onHostPause()
 */
export declare const skipActivityIdentityAssertionOnHostPause: Getter<boolean>;
export declare type skipActivityIdentityAssertionOnHostPause = typeof skipActivityIdentityAssertionOnHostPause;
/**
 * Override getClipBounds on Android views to return the padding box when overflow is hidden
 */
export declare const syncAndroidClipBoundsWithOverflow: Getter<boolean>;
export declare type syncAndroidClipBoundsWithOverflow = typeof syncAndroidClipBoundsWithOverflow;
/**
 * Enables storing js caller stack when creating promise in native module. This is useful in case of Promise rejection and tracing the cause.
 */
export declare const traceTurboModulePromiseRejectionsOnAndroid: Getter<boolean>;
export declare type traceTurboModulePromiseRejectionsOnAndroid = typeof traceTurboModulePromiseRejectionsOnAndroid;
/**
 * When enabled, runtime shadow node references will be updated during the commit. This allows running RSNRU from any thread without corrupting the renderer state.
 */
export declare const updateRuntimeShadowNodeReferencesOnCommit: Getter<boolean>;
export declare type updateRuntimeShadowNodeReferencesOnCommit = typeof updateRuntimeShadowNodeReferencesOnCommit;
/**
 * When enabled, runtime shadow node references will be updated during the commit only on the allowed thread.
 */
export declare const updateRuntimeShadowNodeReferencesOnCommitThread: Getter<boolean>;
export declare type updateRuntimeShadowNodeReferencesOnCommitThread = typeof updateRuntimeShadowNodeReferencesOnCommitThread;
/**
 * In Bridgeless mode, use the always available javascript error reporting pipeline.
 */
export declare const useAlwaysAvailableJSErrorHandling: Getter<boolean>;
export declare type useAlwaysAvailableJSErrorHandling = typeof useAlwaysAvailableJSErrorHandling;
/**
 * Should this application enable the Fabric Interop Layer for Android? If yes, the application will behave so that it can accept non-Fabric components and render them on Fabric. This toggle is controlling extra logic such as custom event dispatching that are needed for the Fabric Interop Layer to work correctly.
 */
export declare const useFabricInterop: Getter<boolean>;
export declare type useFabricInterop = typeof useFabricInterop;
/**
 * When enabled, the native view configs are used in bridgeless mode.
 */
export declare const useNativeViewConfigsInBridgelessMode: Getter<boolean>;
export declare type useNativeViewConfigsInBridgelessMode = typeof useNativeViewConfigsInBridgelessMode;
/**
 * When enabled, ReactScrollView will extend NestedScrollView instead of ScrollView on Android for improved nested scrolling support.
 */
export declare const useNestedScrollViewAndroid: Getter<boolean>;
export declare type useNestedScrollViewAndroid = typeof useNestedScrollViewAndroid;
/**
 * Use shared animation backend in C++ Animated
 */
export declare const useSharedAnimatedBackend: Getter<boolean>;
export declare type useSharedAnimatedBackend = typeof useSharedAnimatedBackend;
/**
 * Use Trait::hidden on Android
 */
export declare const useTraitHiddenOnAndroid: Getter<boolean>;
export declare type useTraitHiddenOnAndroid = typeof useTraitHiddenOnAndroid;
/**
 * In Bridgeless mode, should legacy NativeModules use the TurboModule system?
 */
export declare const useTurboModuleInterop: Getter<boolean>;
export declare type useTurboModuleInterop = typeof useTurboModuleInterop;
/**
 * Outset the culling context frame with the provided ratio. The culling context frame size will be outset by width * ratio on the left and right, and height * ratio on the top and bottom.
 */
export declare const viewCullingOutsetRatio: Getter<number>;
export declare type viewCullingOutsetRatio = typeof viewCullingOutsetRatio;
/**
 * Enable the View Transition API for animating transitions between views.
 */
export declare const viewTransitionEnabled: Getter<boolean>;
export declare type viewTransitionEnabled = typeof viewTransitionEnabled;
/**
 * Use hardware bitmaps for view transition snapshots on Android.
 */
export declare const viewTransitionUseHardwareBitmapAndroid: Getter<boolean>;
export declare type viewTransitionUseHardwareBitmapAndroid = typeof viewTransitionUseHardwareBitmapAndroid;
/**
 * Initial prerender ratio for VirtualView.
 */
export declare const virtualViewPrerenderRatio: Getter<number>;
export declare type virtualViewPrerenderRatio = typeof virtualViewPrerenderRatio;
/**
 * Overrides the feature flags with the provided methods.
 * NOTE: Only JS-only flags can be overridden from JavaScript using this API.
 */
export declare const override: typeof setOverrides;
export declare type override = typeof override;
