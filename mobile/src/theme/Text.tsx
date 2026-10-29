import React from 'react';
import {
  Text as RNText,
  TextInput as RNTextInput,
  TextInputProps,
  TextProps,
} from 'react-native';
import { mono } from './tokens';

/** `Text` with the terminal font; an explicit `fontFamily` in `style` still wins. */
export function Text({ style, ...rest }: TextProps) {
  return <RNText {...rest} style={[{ fontFamily: mono }, style]} />;
}

/** `TextInput` with the terminal font. */
export function TextInput({ style, ...rest }: TextInputProps) {
  return <RNTextInput {...rest} style={[{ fontFamily: mono }, style]} />;
}
