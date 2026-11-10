import '@testing-library/jest-dom'
// Keep contract tests independent of the deployed Gateway.
process.env.NEXT_PUBLIC_API_URL = '';
