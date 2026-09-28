const assert = require('assert');
const fs = require('fs');
const vm = require('vm');

const context = vm.createContext({ URL, fetch: async () => ({}), location: { origin: 'http://localhost:8080' } });
context.window = context;
vm.runInContext(fs.readFileSync('web/dashboard-auth.js', 'utf8'), context);
const factory = context.PlannerAuthFactory;

assert.deepEqual(JSON.parse(JSON.stringify(factory.keycloakConfig({
  issuer: 'http://localhost:8081/realms/financial-planner', realm: 'financial-planner', clientId: 'planner-web',
}))), { url: 'http://localhost:8081', realm: 'financial-planner', clientId: 'planner-web' });
assert.equal(factory.keycloakConfig({ issuer: 'http://identity.example/realms/planner', realm: 'planner', clientId: 'planner-web' }), null);
assert.equal(factory.keycloakConfig({ issuer: 'https://sso.example/realms/other', realm: 'planner', clientId: 'planner-web' }), null);

(async () => {
  const requests = [];
  let updates = 0;
  let cleared = 0;
  let unauthorized = 0;
  const client = {
    authenticated: true,
    token: '',
    async updateToken() { this.token = `access-${++updates}`; },
    clearToken() { this.authenticated = false; cleared++; },
  };
  const fetchImpl = async (url, options) => {
    requests.push({ url, options });
    return { status: url.endsWith('/expired') ? 401 : 200 };
  };
  const request = factory.createAuthenticatedFetch({ client, fetchImpl, onUnauthorized: () => unauthorized++ });
  const controller = new AbortController();
  await request('/api/data-import/validate', {
    method: 'POST', body: new FormData(), signal: controller.signal,
    headers: { 'Idempotency-Key': 'retry-key', 'X-CSRF-Token': 'old-session-token' },
  });
  assert.equal(requests[0].options.credentials, 'omit');
  assert.equal(requests[0].options.headers.Authorization, 'Bearer access-1');
  assert.equal(requests[0].options.headers['Idempotency-Key'], 'retry-key');
  assert.equal(Object.keys(requests[0].options.headers).some(name => name.toLowerCase() === 'x-csrf-token'), false);
  assert.equal(requests[0].options.signal, controller.signal);
  assert.equal(requests[0].options.body instanceof FormData, true);

  const response = await request('/api/expired');
  assert.equal(response.status, 401);
  assert.equal(requests[1].options.headers.Authorization, 'Bearer access-2');
  assert.equal(cleared, 1);
  assert.equal(unauthorized, 1);

  let logoutIdToken = null;
  let logoutOptions = null;
  class MockKeycloak {
    constructor(config) { this.config = config; this.idToken = 'in-memory-id-token'; }
    async init(options) { this.initOptions = options; this.authenticated = true; return true; }
    async logout(options) { logoutIdToken = this.idToken; logoutOptions = options; }
    clearToken() { this.idToken = null; }
  }
  const auth = factory.createPlannerAuth({
    KeycloakCtor: MockKeycloak,
    runtime: { issuer: 'https://sso.example/realms/planner', realm: 'planner', clientId: 'planner-web' },
    location: { origin: 'https://planner.example' },
  });
  assert.equal(await auth.init(), true);
  assert.deepEqual(JSON.parse(JSON.stringify(auth.client.initOptions)), {
    flow: 'standard', pkceMethod: 'S256', responseMode: 'fragment', checkLoginIframe: false,
  });
  await auth.logout();
  assert.equal(logoutIdToken, 'in-memory-id-token', 'the adapter retains the ID token long enough to form a proper end-session request');
  assert.deepEqual(JSON.parse(JSON.stringify(logoutOptions)), { redirectUri: 'https://planner.example/' });
  assert.equal(auth.authenticated, false);

  const html = fs.readFileSync('web/index.html', 'utf8');
  const app = fs.readFileSync('web/app.js', 'utf8');
  const authSource = fs.readFileSync('web/dashboard-auth.js', 'utf8');
  const entry = fs.readFileSync('web/dashboard-auth-entry.js', 'utf8');
  assert(html.includes('/runtime-config.js') && html.includes('/dist/dashboard-auth.bundle.js'));
  assert(!/name="password"|id="login-form"/.test(html));
  assert(!/api\('\/(?:login|session|logout)'/.test(app));
  assert(app.includes("api('/me')") && app.includes("api('/owner-claim'"));
  assert(app.includes('PlannerAuth.init()') && app.includes('await loadIdentity();await load()'));
  assert(authSource.includes("credentials: 'omit'") && authSource.includes('Bearer ${client.token}'));
  assert(authSource.includes("pkceMethod: 'S256'") && !/localStorage|sessionStorage/.test(authSource + entry));
  assert(entry.includes('PUBLIC_CONFIG?.keycloak'));
  console.log('PASS: runtime Keycloak config, memory-only Bearer auth, no cookies/CSRF, expiry clearing, logout, bootstrap and owner claim UI wiring');
})().catch(error => { console.error(error); process.exitCode = 1; });
