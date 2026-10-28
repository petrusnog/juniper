import { useColorScheme } from 'react-native';
import { dark, light, Palette } from './tokens';

/** The palette that follows the system theme. */
export function useTheme(): Palette {
  return useColorScheme() === 'dark' ? dark : light;
}
