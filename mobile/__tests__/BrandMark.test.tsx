import React from 'react';
import { AccessibilityInfo } from 'react-native';
import ReactTestRenderer, { act } from 'react-test-renderer';
import { BLINK_MS, BrandMark } from '../src/ui/BrandMark';
import { terminal } from '../src/theme/tokens';
import { SetupScreen } from '../src/ui/SetupScreen';

jest.useFakeTimers();

type Listener = (on: boolean) => void;

let reduceListener: Listener | null = null;

function mockReduceMotion(initial: boolean) {
  reduceListener = null;
  jest
    .spyOn(AccessibilityInfo, 'isReduceMotionEnabled')
    .mockResolvedValue(initial);
  jest.spyOn(AccessibilityInfo, 'addEventListener').mockImplementation(((
    name: string,
    listener: Listener,
  ) => {
    if (name === 'reduceMotionChanged') {
      reduceListener = listener;
    }
    return { remove: jest.fn() };
  }) as never);
}

async function mount(element: React.ReactElement) {
  let tree!: ReactTestRenderer.ReactTestRenderer;
  await act(async () => {
    tree = ReactTestRenderer.create(element);
  });
  return tree;
}

async function unmount(tree: ReactTestRenderer.ReactTestRenderer) {
  await act(async () => {
    tree.unmount();
  });
}

function cursorOpacity(tree: ReactTestRenderer.ReactTestRenderer): number {
  const style = tree.root.findByProps({ testID: 'brand-cursor' }).props.style;
  const flat = Object.assign({}, ...[style].flat(Infinity).filter(Boolean));
  return Number(flat.opacity?.__getValue?.() ?? flat.opacity);
}

function textOf(tree: ReactTestRenderer.ReactTestRenderer): string[] {
  return tree.root
    .findAll(
      node => typeof node.type === 'string' && (node.type as string) === 'Text',
    )
    .map(node => node.children.join(''));
}

beforeEach(() => {
  mockReduceMotion(false);
});

afterEach(() => {
  jest.clearAllTimers();
  jest.restoreAllMocks();
});

test('shows the logo, the name at its right and the cursor, in the neon green', async () => {
  const tree = await mount(<BrandMark />);
  expect(tree.root.findByProps({ testID: 'brand-logo' })).toBeTruthy();
  expect(textOf(tree)).toEqual(['Juniper', '_']);
  const mark = tree.root.findByProps({ testID: 'brand-mark' });
  expect(mark.props.accessibilityLabel).toBe('Juniper'); // one stop for a screen reader
  const name = tree.root.findAll(
    node =>
      (node.type as string) === 'Text' && node.children.join('') === 'Juniper',
  )[0];
  const flat = Object.assign(
    {},
    ...[name.props.style].flat(Infinity).filter(Boolean),
  );
  expect(flat.color).toBe(terminal.accent);
  await unmount(tree);
});

test('the large mark is for the first-run screen', async () => {
  const small = await mount(<BrandMark />);
  const large = await mount(<BrandMark size="large" />);
  const side = (tree: ReactTestRenderer.ReactTestRenderer) =>
    tree.root.findByProps({ testID: 'brand-logo' }).props.style.width;
  expect(side(large)).toBeGreaterThan(side(small));
  await unmount(small);
  await unmount(large);
});

test('the cursor blinks like a terminal one: on, off, on', async () => {
  const tree = await mount(<BrandMark />);
  expect(cursorOpacity(tree)).toBe(1);
  await act(async () => {
    jest.advanceTimersByTime(BLINK_MS + 50);
  });
  expect(cursorOpacity(tree)).toBe(0);
  await act(async () => {
    jest.advanceTimersByTime(BLINK_MS);
  });
  expect(cursorOpacity(tree)).toBe(1);
  await unmount(tree);
});

test('with "reduce motion" on, the cursor stays still and visible', async () => {
  mockReduceMotion(true);
  const tree = await mount(<BrandMark />);
  await act(async () => {
    jest.advanceTimersByTime(BLINK_MS * 4);
  });
  expect(cursorOpacity(tree)).toBe(1);
  await unmount(tree);
});

test('turning "reduce motion" on while the app is open stops the blinking', async () => {
  const tree = await mount(<BrandMark />);
  await act(async () => {
    jest.advanceTimersByTime(BLINK_MS + 50);
  });
  expect(cursorOpacity(tree)).toBe(0);
  await act(async () => {
    reduceListener?.(true);
  });
  expect(cursorOpacity(tree)).toBe(1);
  await unmount(tree);
});

test('blink={false} keeps the cursor visible', async () => {
  const tree = await mount(<BrandMark blink={false} />);
  await act(async () => {
    jest.advanceTimersByTime(BLINK_MS * 4);
  });
  expect(cursorOpacity(tree)).toBe(1);
  await unmount(tree);
});

async function timersLeftAfterUnmount(blink: boolean): Promise<number> {
  const tree = await mount(<BrandMark blink={blink} />);
  await unmount(tree);
  await act(async () => {
    jest.advanceTimersByTime(BLINK_MS * 8);
  });
  const left = jest.getTimerCount();
  jest.clearAllTimers();
  return left;
}

test('stops the animation when the mark goes away (the loop leaves no timer behind)', async () => {
  // The same count as a mark that never blinked: whatever is left belongs to React, not to us.
  expect(await timersLeftAfterUnmount(true)).toBe(
    await timersLeftAfterUnmount(false),
  );
});

test('the first-run screen starts with the large mark', async () => {
  const tree = await mount(<SetupScreen onSave={jest.fn()} />);
  const mark = tree.root.findByProps({ testID: 'brand-mark' });
  expect(mark).toBeTruthy();
  const logo = tree.root.findByProps({ testID: 'brand-logo' });
  expect(logo.props.style.width).toBeGreaterThanOrEqual(96);
  await unmount(tree);
});
