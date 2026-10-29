module.exports = {
  preset: '@react-native/jest-preset',
  // The first test of a screen file pays for loading react-native on the CI runner (a few
  // seconds): the default 5 s made `VoiceScreen.test.tsx` fail there once, which also blocks
  // the release of the app (it waits for the "Mobile" workflow to be green).
  testTimeout: 20000,
};
