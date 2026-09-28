/* Telegram-link-only UI controller. This surface never calls legacy ledger APIs. */
(function(global){
  const statusPath='/api/me/telegram-link';
  const challengePath='/api/me/telegram-link/challenge';

  function validTelegramDeepLink(value,challenge){
    try{
      const url=new URL(value);
      return /^[A-Za-z0-9_-]{32,128}$/.test(challenge)&&url.protocol==='https:'&&url.hostname==='t.me'&&!url.username&&!url.password&&
        url.pathname.split('/').filter(Boolean).length===1&&
        url.searchParams.get('start')===`link_${challenge}`&&!url.hash;
    }catch{return false;}
  }

  function keycloakClientConfig(runtime={}){
    try{
      const issuer=String(runtime.issuer||'').replace(/\/+$/,'');
      const realm=String(runtime.realm||'');
      const clientId=String(runtime.clientId||'');
      if(!issuer||!realm||!clientId)return null;
      const url=new URL(issuer);
      const loopback=['localhost','127.0.0.1','[::1]'].includes(url.hostname);
      if((url.protocol!=='https:'&&!(loopback&&url.protocol==='http:'))||url.search||url.hash||url.username||url.password)return null;
      const realmMarker=url.pathname.lastIndexOf('/realms/');
      if(!/^[A-Za-z0-9._-]+$/.test(realm)||realmMarker<0||!/^\/realms\/[^/]+$/.test(url.pathname.slice(realmMarker)))return null;
      const basePath=url.pathname.slice(0,realmMarker);
      if(decodeURIComponent(url.pathname.slice(realmMarker).split('/').at(-1))!==realm||(basePath&&!/^\/[A-Za-z0-9._/-]+$/.test(basePath)))return null;
      if(!/^[A-Za-z0-9._-]+$/.test(clientId))return null;
      return {url:`${url.origin}${basePath}`,realm,clientId};
    }catch{return null;}
  }

  function createTelegramLinkController({client,fetchImpl=global.fetch,doc=global.document,now=()=>Date.now(),setTimer=global.setInterval,clearTimer=global.clearInterval}){
    const el=Object.fromEntries(['telegram-link-status','telegram-link-detail','keycloak-sign-in','telegram-connect','telegram-deep-link','telegram-refresh','telegram-unlink','telegram-unlink-dialog','telegram-unlink-close','telegram-unlink-cancel','telegram-unlink-confirm','telegram-unlink-error'].map(id=>[id,doc.getElementById(id)]));
    const state={connected:false,pending:null,busy:false,timer:null,statusLoaded:false};
    const setStatus=(message)=>{el['telegram-link-status'].textContent=message;};
    const show=(id,value)=>{el[id].hidden=!value;};

    function render(){
      const authenticated=Boolean(client.authenticated);
      show('keycloak-sign-in',!authenticated);
      show('telegram-connect',authenticated&&state.statusLoaded&&!state.connected&&!state.pending&&!state.busy);
      show('telegram-deep-link',authenticated&&!state.connected&&Boolean(state.pending));
      show('telegram-refresh',authenticated&&!state.busy);
      show('telegram-unlink',authenticated&&state.connected&&!state.busy);
      el['telegram-link-detail'].textContent=state.pending?`Finish the step in Telegram. This link expires ${new Date(state.pending.expiresAt).toLocaleString()}. You can leave this page open; its status will refresh when you return.`:'';
    }

    async function request(path,method='GET'){
      if(path!==statusPath&&path!==challengePath)throw new Error('This page can only manage the Telegram connection.');
      if(!client.authenticated)throw new Error('Sign in with Keycloak to manage your Telegram connection.');
      await client.updateToken(30);
      if(!client.token)throw new Error('Keycloak did not provide an access token. Please sign in again.');
      const response=await fetchImpl(path,{method,credentials:'omit',headers:{Authorization:`Bearer ${client.token}`,Accept:'application/json'}});
      let body={};try{body=await response.json();}catch{}
      if(!response.ok){
        if(response.status===401){client.clearToken();throw new Error('Your Keycloak session expired. Sign in again to continue.');}
        const detail=body.detail;
        throw new Error(typeof detail==='string'?detail:detail?.message||body.message||`Telegram linking request failed (${response.status}).`);
      }
      return body;
    }

    function clearPending(){
      state.pending=null;
      el['telegram-deep-link'].removeAttribute('href');
      if(state.timer!==null){clearTimer(state.timer);state.timer=null;}
    }

    async function refreshStatus(){
      if(!client.authenticated||state.busy)return;
      state.busy=true;render();
      try{
        const body=await request(statusPath);
        if(typeof body.connected!=='boolean')throw new Error('The Telegram-link status response was incomplete.');
        state.statusLoaded=true;
        state.connected=body.connected;
        if(body.connected){clearPending();setStatus('Telegram is connected to this Keycloak account.');}
        else if(state.pending&&Date.parse(state.pending.expiresAt)>now())setStatus('Telegram connection pending. Finish the step in Telegram, then return here.');
        else{const expired=Boolean(state.pending);clearPending();setStatus(expired?'The Telegram link expired. Create a new link to try again.':'No Telegram account is connected.');}
      }catch(error){setStatus(error.message||'Unable to check the Telegram connection.');}
      finally{state.busy=false;render();}
    }

    async function createChallenge(){
      if(!client.authenticated||state.busy||state.connected)return;
      state.busy=true;render();setStatus('Creating a secure Telegram link…');
      try{
        const body=await request(challengePath,'POST');
        const expiresAt=Date.parse(body.expires_at);
        if(typeof body.challenge!=='string'||typeof body.bot_url!=='string'||!Number.isFinite(expiresAt)||expiresAt<=now())throw new Error('The Telegram-link response was incomplete or expired.');
        if(!validTelegramDeepLink(body.bot_url,body.challenge))throw new Error('The API returned an invalid Telegram bot link. Contact your administrator.');
        clearPending();state.pending={expiresAt:body.expires_at};
        el['telegram-deep-link'].href=body.bot_url;
        setStatus('Telegram connection pending. Open the bot and send the prefilled message to confirm.');
        if(Number(body.expires_in_seconds)>0)state.timer=setTimer(()=>refreshStatus(),5000);
      }catch(error){setStatus(error.message||'Unable to create a Telegram link.');}
      finally{state.busy=false;render();}
    }

    async function unlink(){
      if(!client.authenticated||state.busy||!state.connected)return;
      state.busy=true;render();el['telegram-unlink-error'].textContent='';
      try{
        await request(statusPath,'DELETE');
        state.connected=false;clearPending();
        el['telegram-unlink-dialog'].close();
        setStatus('Telegram has been unlinked from this Keycloak account.');
      }catch(error){el['telegram-unlink-error'].textContent=error.message||'Unable to unlink Telegram.';}
      finally{state.busy=false;render();}
    }

    function start(){
      el['keycloak-sign-in'].addEventListener('click',()=>Promise.resolve(client.login({redirectUri:`${global.location.origin}/telegram-link.html`})).catch(error=>setStatus(error?.message||'Unable to start Keycloak sign-in.')));
      el['telegram-connect'].addEventListener('click',createChallenge);
      el['telegram-refresh'].addEventListener('click',refreshStatus);
      el['telegram-unlink'].addEventListener('click',()=>el['telegram-unlink-dialog'].showModal());
      el['telegram-unlink-close'].addEventListener('click',()=>el['telegram-unlink-dialog'].close());
      el['telegram-unlink-cancel'].addEventListener('click',()=>el['telegram-unlink-dialog'].close());
      el['telegram-unlink-confirm'].addEventListener('click',unlink);
      global.addEventListener('focus',()=>{if(client.authenticated)refreshStatus();});
      doc.addEventListener('visibilitychange',()=>{if(!doc.hidden&&client.authenticated)refreshStatus();});
      if(client.authenticated)refreshStatus();
      else{setStatus('Sign in with Keycloak to manage your Telegram connection.');render();}
      return {refreshStatus,createChallenge,unlink,state};
    }

    return {start,refreshStatus,createChallenge,unlink,state};
  }

  global.TelegramLinkUI={createTelegramLinkController,validTelegramDeepLink,keycloakClientConfig};
})(window);
