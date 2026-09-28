import Keycloak from 'keycloak-js';
import './telegram-link-ui.js';

const status=document.getElementById('telegram-link-status');
const signIn=document.getElementById('keycloak-sign-in');
const runtime=window.PUBLIC_CONFIG?.keycloak||{};

function notConfigured(){
  status.textContent='Keycloak sign-in is not configured for this deployment.';
  const detail=document.getElementById('telegram-link-detail');
  detail.textContent='An administrator must provide the public Keycloak issuer, realm, and SPA client ID before Telegram linking can be used.';
}

async function initialize(){
  const config=window.TelegramLinkUI.keycloakClientConfig(runtime);
  if(!config){notConfigured();return;}
  try{
    const client=new Keycloak(config);
    await client.init({flow:'standard',pkceMethod:'S256',responseMode:'fragment',checkLoginIframe:false});
    window.TelegramLinkUI.createTelegramLinkController({client}).start();
  }catch(error){
    status.textContent='Keycloak could not be initialized.';
    document.getElementById('telegram-link-detail').textContent=error?.message||'Check the Keycloak URL, client configuration, network connection, and redirect URI.';
    signIn.hidden=true;
  }
}

initialize();
