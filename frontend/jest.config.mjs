import nextJest from 'next/jest.js'
const createJestConfig = nextJest({ dir: './' })
// The AI SDK and zod ship ES modules that Jest cannot load as they are. next/jest leaves
// node_modules as is, so they are exempted from that and compiled by SWC like the application code.
const ESM_ONLY_PACKAGES = '(?!(?:@ai-sdk|@workflow|ai|zod)/)'
export default async () => {
  const config = await createJestConfig({
    coverageProvider: 'v8',
    testEnvironment: '<rootDir>/jest.environment.cjs',
    setupFilesAfterEnv: ['<rootDir>/jest.setup.ts'],
    modulePathIgnorePatterns: ['<rootDir>/.next/'],
    collectCoverageFrom: ['lib/**/*.{ts,tsx}', 'app/api/**/*.{ts,tsx}', 'components/**/*.{ts,tsx}', 'proxy.ts', '!**/*.d.ts'],
  })()
  const [nodeModules, ...rest] = config.transformIgnorePatterns
  return { ...config, transformIgnorePatterns: [nodeModules.replace('(?!.pnpm)', `(?!.pnpm)${ESM_ONLY_PACKAGES}`), ...rest] }
}
