import { sendJson, type Route } from './server.js';

export const HEALTH_PATH = '/healthz';

/** Liveness of the process; carries no dependency checks because the service has no database. */
export function healthRoute(): Route {
  return {
    method: 'GET',
    path: HEALTH_PATH,
    accessLog: 'debug',
    handle(_req, res) {
      sendJson(res, 200, { status: 'ok' });
    },
  };
}
