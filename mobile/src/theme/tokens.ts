/**
 * Terminal look of the app: black background, neon green like the logo, and the Tango
 * palette of Linux terminals for the other colors (docs: Identidade visual).
 */
export interface Palette {
  background: string;
  card: string;
  ink: string;
  muted: string;
  border: string;
  /** Neon green of the logo: primary actions, the active tab, the user's text. */
  accent: string;
  /** Text drawn on top of an accent-colored button. */
  onAccent: string;
  cyan: string;
  blue: string;
  violet: string;
  green: string;
  amber: string;
  red: string;
}

export const terminal: Palette = {
  background: '#000000',
  card: '#06100a',
  ink: '#d3f2cc',
  muted: '#6f9a6a',
  border: '#1d6b2b',
  accent: '#39e639',
  onAccent: '#000000',
  cyan: '#34e2e2',
  blue: '#729fcf',
  violet: '#ad7fa8',
  green: '#8ae234',
  amber: '#fce94f',
  red: '#ef2929',
};

export const spacing = { xs: 4, sm: 8, md: 12, lg: 16, xl: 24 } as const;
export const radius = { card: 3, bubble: 3 } as const;
export const mono = 'monospace';
