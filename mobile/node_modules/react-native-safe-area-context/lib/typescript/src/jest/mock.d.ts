import React from 'react';
import type { Metrics } from '../SafeArea.types';
import type { SafeAreaProviderProps, SafeAreaInsetsContext, SafeAreaFrameContext } from '../SafeAreaContext';
declare const _default: {
    initialWindowMetrics: Metrics;
    useSafeAreaInsets: import("jest-mock").Mock<() => import("../SafeArea.types").EdgeInsets>;
    useSafeAreaFrame: import("jest-mock").Mock<() => import("../SafeArea.types").Rect>;
    SafeAreaProvider: ({ children, initialMetrics }: SafeAreaProviderProps) => React.JSX.Element;
    SafeAreaInsetsContext: typeof SafeAreaInsetsContext;
    SafeAreaFrameContext: typeof SafeAreaFrameContext;
};
export default _default;
//# sourceMappingURL=mock.d.ts.map