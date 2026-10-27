/** Reconnect delay: 1, 2, 4, 8, 16 and then 30 s, +-20% (docs/pages/protocolo.md, reconnection). */
const STEPS = [1000, 2000, 4000, 8000, 16000, 30000];

export function delayFor(
  attempt: number,
  random: () => number = Math.random,
): number {
  const base = STEPS[Math.min(attempt, STEPS.length - 1)];
  return Math.round(base * (0.8 + 0.4 * random()));
}
