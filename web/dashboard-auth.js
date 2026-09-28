/* Keycloak-only dashboard authentication. Tokens stay in this page's memory. */
(function (global) {
  function keycloakConfig(runtime = {}) {
    try {
      const issuer = String(runtime.issuer || '').replace(/\/+$/, '');
      const realm = String(runtime.realm || '');
      const clientId = String(runtime.clientId || '');
      if (!issuer || !realm || !clientId) return null;
      const url = new URL(issuer);
      const loopback = ['localhost', '127.0.0.1', '[::1]'].includes(url.hostname);
      if ((url.protocol !== 'https:' && !(loopback && url.protocol === 'http:')) || url.search || url.hash || url.username || url.password) return null;
      const marker = url.pathname.lastIndexOf('/realms/');
      if (!/^[A-Za-z0-9._-]+$/.test(realm) || marker < 0 || !/^\/realms\/[^/]+$/.test(url.pathname.slice(marker))) return null;
      const basePath = url.pathname.slice(0, marker);
      if (decodeURIComponent(url.pathname.slice(marker).split('/').at(-1)) !== realm || (basePath && !/^\/[A-Za-z0-9._/-]+$/.test(basePath))) return null;
      if (!/^[A-Za-z0-9._-]+$/.test(clientId)) return null;
      return { url: `${url.origin}${basePath}`, realm, clientId };
    } catch { return null; }
  }

  function createAuthenticatedFetch({ client, fetchImpl = global.fetch, onUnauthorized = () => {} }) {
    return async function authenticatedFetch(url, options = {}) {
      if (!client?.authenticated) {
        const error = new Error('Sign in with your Keycloak account to continue.');
        error.status = 401;
        throw error;
      }
      try {
        await client.updateToken(30);
      } catch (cause) {
        client.clearToken();
        onUnauthorized(cause);
        const error = new Error('Your sign-in expired. Sign in again to continue.');
        error.status = 401;
        throw error;
      }
      if (!client.authenticated || !client.token) {
        client.clearToken();
        onUnauthorized();
        const error = new Error('Your sign-in expired. Sign in again to continue.');
        error.status = 401;
        throw error;
      }
      const headers = { ...(options.headers || {}) };
      for (const key of Object.keys(headers)) if (key.toLowerCase() === 'x-csrf-token') delete headers[key];
      headers.Authorization = `Bearer ${client.token}`;
      const response = await fetchImpl(url, { ...options, credentials: 'omit', headers });
      if (response.status === 401) {
        client.clearToken();
        onUnauthorized();
      }
      return response;
    };
  }

  function createPlannerAuth({ KeycloakCtor, runtime = {}, location = global.location }) {
    const config = keycloakConfig(runtime);
    const auth = {
      configured: Boolean(config),
      config,
      client: null,
      request: null,
      authenticated: false,
      onUnauthorized: () => {},
      async init() {
        if (!config) return false;
        auth.client = new KeycloakCtor(config);
        auth.client.onTokenExpired = () => auth.client.updateToken(30).catch(error => {
          auth.client.clearToken();
          auth.authenticated = false;
          auth.onUnauthorized(error);
        });
        auth.client.onAuthRefreshError = () => {
          auth.client.clearToken();
          auth.authenticated = false;
          auth.onUnauthorized();
        };
        auth.authenticated = await auth.client.init({ flow: 'standard', pkceMethod: 'S256', responseMode: 'fragment', checkLoginIframe: false });
        auth.request = createAuthenticatedFetch({
          client: auth.client,
          onUnauthorized: cause => {
            auth.authenticated = false;
            auth.onUnauthorized(cause);
          },
        });
        return auth.authenticated;
      },
      login() {
        if (!auth.client) throw new Error('Keycloak sign-in is not configured.');
        return auth.client.login({ redirectUri: `${location.origin}/` });
      },
      async logout() {
        auth.authenticated = false;
        if (auth.client) {
          // The adapter needs its in-memory ID token to build the Keycloak end-session URL.
          auth.client.authenticated = false;
          try { return await auth.client.logout({ redirectUri: `${location.origin}/` }); }
          catch (error) { auth.client.clearToken(); throw error; }
        }
      },
    };
    return auth;
  }

  global.PlannerAuthFactory = { keycloakConfig, createAuthenticatedFetch, createPlannerAuth };
})(window);
