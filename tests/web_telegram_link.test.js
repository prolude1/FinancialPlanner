const assert=require('assert');
const fs=require('fs');
const vm=require('vm');

const context=vm.createContext({URL,setInterval:()=>17,clearInterval:()=>{},Date});
context.window=context;
context.location={origin:'https://planner.example'};
context.addEventListener=()=>{};
vm.runInContext(fs.readFileSync('web/telegram-link-ui.js','utf8'),context);

assert.equal(vm.runInContext("TelegramLinkUI.validTelegramDeepLink('https://t.me/finance_bot?start=link_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-','ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-')",context),true);
assert.equal(vm.runInContext("TelegramLinkUI.validTelegramDeepLink('https://evil.example/finance_bot?start=link_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-','ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-')",context),false);
assert.equal(vm.runInContext("TelegramLinkUI.validTelegramDeepLink('javascript:alert(1)','ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-')",context),false);
const rootRealm=vm.runInContext("TelegramLinkUI.keycloakClientConfig({issuer:'https://sso.example/realms/financial-planner',realm:'financial-planner',clientId:'financial-planner-spa'})",context);
assert.deepEqual(JSON.parse(JSON.stringify(rootRealm)),{url:'https://sso.example',realm:'financial-planner',clientId:'financial-planner-spa'});
const pathRealm=vm.runInContext("TelegramLinkUI.keycloakClientConfig({issuer:'https://sso.example/auth/realms/financial-planner',realm:'financial-planner',clientId:'financial-planner-spa'})",context);
assert.equal(pathRealm.url,'https://sso.example/auth');
assert.equal(vm.runInContext("TelegramLinkUI.keycloakClientConfig({issuer:'',realm:'',clientId:''})",context),null);

function element(){return {hidden:true,textContent:'',href:'',events:{},addEventListener(name,fn){this.events[name]=fn;},removeAttribute(name){delete this[name];},showModal(){this.open=true;},close(){this.open=false;}};}
const nodes=new Map();
const doc={hidden:false,getElementById(id){if(!nodes.has(id))nodes.set(id,element());return nodes.get(id);},addEventListener(){}};
const calls=[];
let connected=false;
const challenge='ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-';
const client={authenticated:false,token:'',updated:0,cleared:false,async updateToken(){this.updated+=1;this.token=`fresh-access-${this.updated}`;},clearToken(){this.cleared=true;this.authenticated=false;}};
const fetchImpl=async(path,options)=>{
  calls.push({path,options});
  let body={};
  if(options.method==='POST')body={challenge,expires_at:new Date(Date.now()+600000).toISOString(),expires_in_seconds:600,bot_url:`https://t.me/finance_bot?start=link_${challenge}`};
  else if(options.method==='GET')body={connected};
  else if(options.method==='DELETE'){connected=false;body={connected:false};}
  return {ok:true,status:options.method==='POST'?201:200,async json(){return body;}};
};

(async()=>{
  context.controllerOptions={client,fetchImpl,doc,now:()=>Date.now(),setTimer:()=>17,clearTimer:()=>{}};
  const controller=vm.runInContext('TelegramLinkUI.createTelegramLinkController(controllerOptions)',context);
  controller.start();
  assert.equal(nodes.get('keycloak-sign-in').hidden,false);
  client.authenticated=true;
  await controller.refreshStatus();
  assert.equal(controller.state.statusLoaded,true);
  assert.equal(nodes.get('telegram-link-status').textContent,'No Telegram account is connected.');
  await controller.createChallenge();
  assert.equal(controller.state.pending!==null,true);
  assert.equal(nodes.get('telegram-deep-link').href,`https://t.me/finance_bot?start=link_${challenge}`);
  assert.equal(nodes.get('telegram-deep-link').hidden,false);
  assert(nodes.get('telegram-link-status').textContent.includes('pending'));
  await controller.refreshStatus();
  assert.equal(controller.state.pending!==null,true,'an unconfirmed connection stays visibly pending');
  connected=true;
  await controller.refreshStatus();
  assert.equal(controller.state.connected,true);
  assert.equal(controller.state.pending,null);
  assert.equal(nodes.get('telegram-link-status').textContent,'Telegram is connected to this Keycloak account.');
  nodes.get('telegram-unlink').events.click();
  assert.equal(nodes.get('telegram-unlink-dialog').open,true);
  assert.equal(calls.some(call=>call.options.method==='DELETE'),false,'unlink requires confirmation');
  await nodes.get('telegram-unlink-confirm').events.click();
  assert.equal(controller.state.connected,false);
  assert.equal(nodes.get('telegram-link-status').textContent,'Telegram has been unlinked from this Keycloak account.');
  assert.deepEqual(calls.map(call=>[call.path,call.options.method]),[
    ['/api/me/telegram-link','GET'],['/api/me/telegram-link/challenge','POST'],
    ['/api/me/telegram-link','GET'],['/api/me/telegram-link','GET'],['/api/me/telegram-link','DELETE'],
  ]);
  for(const call of calls){
    assert.equal(call.options.credentials,'omit');
    assert.match(call.options.headers.Authorization,/^Bearer fresh-access-/);
  }
  assert.equal(client.updated,calls.length,'a fresh access token is requested for every API call');

  const page=fs.readFileSync('web/telegram-link.html','utf8');
  assert(page.includes('/runtime-config.js'));
  assert(page.includes('/dist/telegram-link.bundle.js'));
  assert(!page.includes('app.js'));
  const ui=fs.readFileSync('web/telegram-link-ui.js','utf8');
  const entry=fs.readFileSync('web/telegram-link-entry.js','utf8');
  assert(!/localStorage|sessionStorage|telegram_user_id|idToken/.test(ui+entry));
  assert(entry.includes("flow:'standard'"));
  assert(entry.includes("pkceMethod:'S256'"));
  assert(entry.includes('not configured'));
  const template=fs.readFileSync('web/nginx.conf.template','utf8');
  assert(template.includes("connect-src 'self' ${KEYCLOAK_ORIGIN}"));
  const compose=fs.readFileSync('compose.yaml','utf8');
  for(const field of ['KEYCLOAK_ISSUER','KEYCLOAK_REALM','KEYCLOAK_WEB_CLIENT_ID','KEYCLOAK_ORIGIN'])assert(compose.includes(field));
  const pkg=JSON.parse(fs.readFileSync('web/package.json','utf8'));
  assert.equal(pkg.dependencies['keycloak-js'],'26.2.4');
  console.log('PASS: Isolated Keycloak linking surface, PKCE config, token-only API access, pending/connected/unlink states, and runtime public config');
})().catch(error=>{console.error(error);process.exitCode=1;});
