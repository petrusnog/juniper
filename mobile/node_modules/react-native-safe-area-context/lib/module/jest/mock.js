import { jest } from '@jest/globals';
import React, { useContext } from 'react';
const MOCK_INITIAL_METRICS = {
  frame: {
    width: 320,
    height: 640,
    x: 0,
    y: 0
  },
  insets: {
    left: 0,
    right: 0,
    bottom: 0,
    top: 0
  }
};
const RNSafeAreaContext = jest.requireActual('react-native-safe-area-context');
export default {
  ...RNSafeAreaContext,
  initialWindowMetrics: MOCK_INITIAL_METRICS,
  useSafeAreaInsets: jest.fn(() => {
    return useContext(RNSafeAreaContext.SafeAreaInsetsContext) ?? MOCK_INITIAL_METRICS.insets;
  }),
  useSafeAreaFrame: jest.fn(() => {
    return useContext(RNSafeAreaContext.SafeAreaFrameContext) ?? MOCK_INITIAL_METRICS.frame;
  }),
  // Provide a simpler implementation with default values.
  SafeAreaProvider: ({
    children,
    initialMetrics
  }) => {
    return /*#__PURE__*/React.createElement(RNSafeAreaContext.SafeAreaFrameContext.Provider, {
      value: initialMetrics?.frame ?? MOCK_INITIAL_METRICS.frame
    }, /*#__PURE__*/React.createElement(RNSafeAreaContext.SafeAreaInsetsContext.Provider, {
      value: initialMetrics?.insets ?? MOCK_INITIAL_METRICS.insets
    }, children));
  }
};
//# sourceMappingURL=mock.js.map