import { Palette, terminal } from './tokens';

/** The terminal palette. The app has a single look, regardless of the system theme. */
export function useTheme(): Palette {
  return terminal;
}
