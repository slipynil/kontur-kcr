// Jest's jsdom environment has neither fetch nor Web Streams. The AI SDK chat transport reads a
// streaming Response in tests, so the Node.js implementations are exposed on the window.
const { TestEnvironment: JsdomEnvironment } = require('jest-environment-jsdom');

class WebStreamsJsdomEnvironment extends JsdomEnvironment {
  async setup() {
    await super.setup();
    Object.assign(this.global, {
      Headers,
      Request,
      Response,
      ReadableStream,
      WritableStream,
      TransformStream,
      TextDecoder,
      TextEncoder,
      TextDecoderStream,
      TextEncoderStream,
    });
  }
}

module.exports = WebStreamsJsdomEnvironment;
