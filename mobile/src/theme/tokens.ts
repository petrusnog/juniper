/** Colors of the pocket book (docs/html/assets/style.css), light and dark (ADR-022, section 8). */
export interface Palette {
  background: string;
  card: string;
  ink: string;
  muted: string;
  border: string;
  blue: string;
  violet: string;
  green: string;
  amber: string;
  red: string;
}

export const light: Palette = {
  background: '#f7f6f2',
  card: '#ffffff',
  ink: '#1d2330',
  muted: '#667085',
  border: '#e3e1d9',
  blue: '#2563eb',
  violet: '#7c3aed',
  green: '#16a34a',
  amber: '#d97706',
  red: '#dc2626',
};

export const dark: Palette = {
  background: '#14171f',
  card: '#1d212c',
  ink: '#e8eaf0',
  muted: '#9aa3b2',
  border: '#2c3242',
  blue: '#60a5fa',
  violet: '#a78bfa',
  green: '#4ade80',
  amber: '#fbbf24',
  red: '#f87171',
};

export const spacing = { xs: 4, sm: 8, md: 12, lg: 16, xl: 24 } as const;
export const radius = { card: 14, bubble: 18 } as const;
export const mono = 'monospace';
