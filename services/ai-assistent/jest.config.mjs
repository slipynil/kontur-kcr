// Jest runs the ESM sources through ts-jest; start it with
// `node --experimental-vm-modules node_modules/jest/bin/jest.js` (see package.json).
export default {
  coverageProvider: 'v8',
  testEnvironment: 'node',
  extensionsToTreatAsEsm: ['.ts'],
  moduleNameMapper: { '^(\\.{1,2}/.*)\\.js$': '$1' },
  transform: {
    '^.+\\.ts$': ['ts-jest', { useESM: true, tsconfig: 'tsconfig.json' }],
  },
  collectCoverageFrom: ['src/**/*.ts', '!src/**/*.d.ts'],
  coverageThreshold: {
    global: { statements: 90 },
  },
  testTimeout: 10000,
};
