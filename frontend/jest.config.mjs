import nextJest from 'next/jest.js'
const createJestConfig = nextJest({ dir: './' })
export default createJestConfig({
  coverageProvider: 'v8',
  testEnvironment: 'node',
  setupFilesAfterEnv: ['<rootDir>/jest.setup.ts'],
  collectCoverageFrom: ['lib/**/*.{ts,tsx}', 'app/api/**/*.{ts,tsx}', 'components/**/*.{ts,tsx}', '!**/*.d.ts'],
})
