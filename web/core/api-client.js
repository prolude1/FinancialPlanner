/** Authenticated JSON transport shared by dashboard features. */
export class AuthenticatedApiClient {
  constructor({ authenticatedFetch }) {
    if (typeof authenticatedFetch !== 'function') {
      throw new TypeError('authenticatedFetch must be a function');
    }
    this.authenticatedFetch = authenticatedFetch;
  }

  async request(path, options = {}) {
    const headers = { ...options.headers };
    if (!(options.body instanceof FormData)) {
      headers['Content-Type'] = 'application/json';
    }

    const response = await this.authenticatedFetch(`/api${path}`, {
      ...options,
      headers,
    });
    const contentType = response.headers.get('content-type') || '';
    const body = contentType.includes('json') ? await response.json() : null;

    if (!response.ok) {
      const message = typeof body?.detail === 'string'
        ? body.detail
        : body?.detail?.message || body?.message
          || 'Request failed. Check the fields and try again.';
      throw Object.assign(new Error(message), {
        status: response.status,
        code: body?.detail?.code || body?.code,
      });
    }

    return contentType.includes('json') ? body : response;
  }
}
