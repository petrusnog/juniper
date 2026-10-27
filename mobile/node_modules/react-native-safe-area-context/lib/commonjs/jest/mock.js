"use strict";

Object.defineProperty(exports, "__esModule", {
  value: true
});
exports.default = void 0;
var _globals = require("@jest/globals");
var _react = _interopRequireWildcard(require("react"));
function _interopRequireWildcard(e, t) { if ("function" == typeof WeakMap) var r = new WeakMap(), n = new WeakMap(); return (_interopRequireWildcard = function (e, t) { if (!t && e && e.__esModule) return e; var o, i, f = { __proto__: null, default: e }; if (null === e || "object" != typeof e && "function" != typeof e) return f; if (o = t ? n : r) { if (o.has(e)) return o.get(e); o.set(e, f); } for (const t in e) "default" !== t && {}.hasOwnProperty.call(e, t) && ((i = (o = Object.defineProperty) && Object.getOwnPropertyDescriptor(e, t)) && (i.get || i.set) ? o(f, t, i) : f[t] = e[t]); return f; })(e, t); }
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
const RNSafeAreaContext = _globals.jest.requireActual('react-native-safe-area-context');
var _default = exports.default = {
  ...RNSafeAreaContext,
  initialWindowMetrics: MOCK_INITIAL_METRICS,
  useSafeAreaInsets: _globals.jest.fn(() => {
    return (0, _react.useContext)(RNSafeAreaContext.SafeAreaInsetsContext) ?? MOCK_INITIAL_METRICS.insets;
  }),
  useSafeAreaFrame: _globals.jest.fn(() => {
    return (0, _react.useContext)(RNSafeAreaContext.SafeAreaFrameContext) ?? MOCK_INITIAL_METRICS.frame;
  }),
  // Provide a simpler implementation with default values.
  SafeAreaProvider: ({
    children,
    initialMetrics
  }) => {
    return /*#__PURE__*/_react.default.createElement(RNSafeAreaContext.SafeAreaFrameContext.Provider, {
      value: initialMetrics?.frame ?? MOCK_INITIAL_METRICS.frame
    }, /*#__PURE__*/_react.default.createElement(RNSafeAreaContext.SafeAreaInsetsContext.Provider, {
      value: initialMetrics?.insets ?? MOCK_INITIAL_METRICS.insets
    }, children));
  }
};
//# sourceMappingURL=mock.js.map