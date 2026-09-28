import Keycloak from 'keycloak-js';
import './dashboard-auth.js';

window.PlannerAuth = window.PlannerAuthFactory.createPlannerAuth({
  KeycloakCtor: Keycloak,
  runtime: window.PUBLIC_CONFIG?.keycloak || {},
});
