/**
 * Copyright (c) Meta Platforms, Inc. and affiliates.
 *
 * This source code is licensed under the MIT license found in the
 * LICENSE file in the root directory of this source tree.
 *
 * @generated SignedSource<<77332752cda558f1020473f04f9bcbb5>>
 *
 * This file was translated from Flow by scripts/js-api/build-types/index.js.
 * Original file: packages/react-native/src/private/featureflags/specs/NativeReactNativeFeatureFlags.js
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

import type { TurboModule } from "../../../../Libraries/TurboModule/RCTExport";
export interface Spec extends TurboModule {
  readonly commonTestFlag?: (() => boolean) | undefined;
  readonly commonTestFlagWithoutNativeImplementation?: (() => boolean) | undefined;
  readonly cdpInteractionMetricsEnabled?: (() => boolean) | undefined;
  readonly cxxNativeAnimatedEnabled?: (() => boolean) | undefined;
  readonly defaultTextToOverflowHidden?: (() => boolean) | undefined;
  readonly disableEarlyViewCommandExecution?: (() => boolean) | undefined;
  readonly disableImageViewPreallocationAndroid?: (() => boolean) | undefined;
  readonly disableMountItemReorderingAndroid?: (() => boolean) | undefined;
  readonly disableSubviewClippingAndroid?: (() => boolean) | undefined;
  readonly disableTextLayoutManagerCacheAndroid?: (() => boolean) | undefined;
  readonly disableViewPreallocationAndroid?: (() => boolean) | undefined;
  readonly enableAccessibilityOrder?: (() => boolean) | undefined;
  readonly enableAccumulatedUpdatesInRawPropsAndroid?: (() => boolean) | undefined;
  readonly enableAndroidTextMeasurementOptimizations?: (() => boolean) | undefined;
  readonly enableBridgelessArchitecture?: (() => boolean) | undefined;
  readonly enableBufferedCallInvoker?: (() => boolean) | undefined;
  readonly enableCppPropsIteratorSetter?: (() => boolean) | undefined;
  readonly enableCustomFocusSearchOnClippedElementsAndroid?: (() => boolean) | undefined;
  readonly enableDestroyShadowTreeRevisionAsync?: (() => boolean) | undefined;
  readonly enableDoubleMeasurementFixAndroid?: (() => boolean) | undefined;
  readonly enableEagerRootViewAttachment?: (() => boolean) | undefined;
  readonly enableExclusivePropsUpdateAndroid?: (() => boolean) | undefined;
  readonly enableFabricCommitBranching?: (() => boolean) | undefined;
  readonly enableFabricLogs?: (() => boolean) | undefined;
  readonly enableFlexboxAutoMinSizeInStrictMode?: (() => boolean) | undefined;
  readonly enableFontScaleChangesUpdatingLayout?: (() => boolean) | undefined;
  readonly enableIOSCompressedTextFrameAdjustment?: (() => boolean) | undefined;
  readonly enableIOSTextBaselineOffsetPerLine?: (() => boolean) | undefined;
  readonly enableIOSViewClipToPaddingBox?: (() => boolean) | undefined;
  readonly enableImagePrefetchingAndroid?: (() => boolean) | undefined;
  readonly enableImageTransparentTintColor?: (() => boolean) | undefined;
  readonly enableImmediateUpdateModeForContentOffsetChanges?: (() => boolean) | undefined;
  readonly enableImperativeEvents?: (() => boolean) | undefined;
  readonly enableImperativeFocus?: (() => boolean) | undefined;
  readonly enableInteropViewManagerClassLookUpOptimizationIOS?: (() => boolean) | undefined;
  readonly enableIntersectionObserverByDefault?: (() => boolean) | undefined;
  readonly enableKeyEvents?: (() => boolean) | undefined;
  readonly enableLayoutAnimationsOnAndroid?: (() => boolean) | undefined;
  readonly enableLayoutAnimationsOnIOS?: (() => boolean) | undefined;
  readonly enableModuleArgumentNSNullConversionIOS?: (() => boolean) | undefined;
  readonly enableMountingCoordinatorPullModelAndroid?: (() => boolean) | undefined;
  readonly enableMutationObserverByDefault?: (() => boolean) | undefined;
  readonly enableNativeCSSParsing?: (() => boolean) | undefined;
  readonly enablePreparedTextLayout?: (() => boolean) | undefined;
  readonly enablePropsUpdateReconciliationAndroid?: (() => boolean) | undefined;
  readonly enableResizeObserverByDefault?: (() => boolean) | undefined;
  readonly enableSwiftUIBasedFilters?: (() => boolean) | undefined;
  readonly enableViewCulling?: (() => boolean) | undefined;
  readonly enableViewRecycling?: (() => boolean) | undefined;
  readonly enableViewRecyclingForImage?: (() => boolean) | undefined;
  readonly enableViewRecyclingForScrollView?: (() => boolean) | undefined;
  readonly enableViewRecyclingForText?: (() => boolean) | undefined;
  readonly enableViewRecyclingForView?: (() => boolean) | undefined;
  readonly enableVirtualViewContainerStateExperimental?: (() => boolean) | undefined;
  readonly fixDifferentiatorParentTagForUnflattenCase?: (() => boolean) | undefined;
  readonly fixMappingOfEventPrioritiesBetweenFabricAndReact?: (() => boolean) | undefined;
  readonly fixYogaFlexBasisFitContentInMainAxis?: (() => boolean) | undefined;
  readonly fuseboxAssertSingleHostState?: (() => boolean) | undefined;
  readonly fuseboxEnabledRelease?: (() => boolean) | undefined;
  readonly fuseboxFrameRecordingEnabled?: (() => boolean) | undefined;
  readonly fuseboxScreenshotCaptureEnabled?: (() => boolean) | undefined;
  readonly fuseboxWebSocketEventsEnabled?: (() => boolean) | undefined;
  readonly optimizedAnimatedPropUpdates?: (() => boolean) | undefined;
  readonly overrideBySynchronousMountPropsAtMountingAndroid?: (() => boolean) | undefined;
  readonly perfIssuesEnabled?: (() => boolean) | undefined;
  readonly perfMonitorV2Enabled?: (() => boolean) | undefined;
  readonly preparedTextCacheSize?: (() => number) | undefined;
  readonly preventShadowTreeCommitExhaustion?: (() => boolean) | undefined;
  readonly redBoxV2Android?: (() => boolean) | undefined;
  readonly redBoxV2IOS?: (() => boolean) | undefined;
  readonly shouldPressibilityUseW3CPointerEventsForHover?: (() => boolean) | undefined;
  readonly shouldTriggerResponderTransferOnScrollAndroid?: (() => boolean) | undefined;
  readonly skipActivityIdentityAssertionOnHostPause?: (() => boolean) | undefined;
  readonly syncAndroidClipBoundsWithOverflow?: (() => boolean) | undefined;
  readonly traceTurboModulePromiseRejectionsOnAndroid?: (() => boolean) | undefined;
  readonly updateRuntimeShadowNodeReferencesOnCommit?: (() => boolean) | undefined;
  readonly updateRuntimeShadowNodeReferencesOnCommitThread?: (() => boolean) | undefined;
  readonly useAlwaysAvailableJSErrorHandling?: (() => boolean) | undefined;
  readonly useFabricInterop?: (() => boolean) | undefined;
  readonly useNativeViewConfigsInBridgelessMode?: (() => boolean) | undefined;
  readonly useNestedScrollViewAndroid?: (() => boolean) | undefined;
  readonly useSharedAnimatedBackend?: (() => boolean) | undefined;
  readonly useTraitHiddenOnAndroid?: (() => boolean) | undefined;
  readonly useTurboModuleInterop?: (() => boolean) | undefined;
  readonly viewCullingOutsetRatio?: (() => number) | undefined;
  readonly viewTransitionEnabled?: (() => boolean) | undefined;
  readonly viewTransitionUseHardwareBitmapAndroid?: (() => boolean) | undefined;
  readonly virtualViewPrerenderRatio?: (() => number) | undefined;
}
declare const NativeReactNativeFeatureFlags: null | undefined | Spec;
declare const $$NativeReactNativeFeatureFlags: typeof NativeReactNativeFeatureFlags;
declare type $$NativeReactNativeFeatureFlags = typeof $$NativeReactNativeFeatureFlags;
export default $$NativeReactNativeFeatureFlags;
