import nextJest from 'next/jest.js'
const createJestConfig = nextJest({ dir: './' })
export default createJestConfig({
  coverageProvider: 'v8',
  testEnvironment: 'jsdom',
  setupFilesAfterEnv: ['<rootDir>/jest.setup.ts'],
  modulePathIgnorePatterns: ['<rootDir>/.next/'],
  collectCoverageFrom: ['lib/**/*.{ts,tsx}', 'app/api/**/*.{ts,tsx}', 'components/**/*.{ts,tsx}', 'proxy.ts', '!**/*.d.ts'],
})
