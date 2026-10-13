const $ = id => document.getElementById(id);
const fmt = n => {let value=Number(n)||0;for(const unit of ['B','KB','MB','GB','TB']){if(value<1024) return `${value.toFixed(value<10&&unit!=='B'?1:0)} ${unit}`;value/=1024;}return `${value.toFixed(1)} PB`;};
let cwd='';
const api=async (path, options={}) => {
  const response=await fetch(path,{...options,headers:{'Content-Type':'application/json',...(options.headers||{})},credentials:'same-origin',cache:'no-store'});
  if(!response.ok) {
    let message=`${response.status} ${response.statusText}`;
    if(response.status === 400 || response.status === 403 || response.status === 422){
      // Only show bounded, server-authored validation messages; never echo the magnet URI.
      const data=await response.json().catch(()=>null);
      if(typeof data?.detail === 'string' && data.detail.length <= 180){
        message=`${response.status}: ${data.detail}`;
      }else if(response.status === 422){
        message='422: Magnet link is invalid or too long (max 4096 characters).';
      }
    }
    throw new Error(message);
  }
  return response.json();
};
const show = (message, error=false) => { $('msg').textContent=message;$('msg').className=error?'message error':'message'; };
const text = (node,value) => node.textContent=String(value);
async function transfers(){
  try{
    const d=await api('/api/transfers'); $('health').textContent='● Online';
    text($('active'),d.items.filter(x=>x.status==='active').length);text($('speed'),`${fmt(d.download_speed)}/s`);
    const body=$('transfers-list');body.replaceChildren();
    if(!d.items.length){const tr=document.createElement('tr'),td=document.createElement('td');td.colSpan=5;td.className='empty';td.textContent='Nothing queued yet. Add a magnet link to begin.';tr.append(td);body.append(tr);}
    for(const item of d.items){
      const tr=document.createElement('tr');
      const name=document.createElement('td');name.className='filename';name.title=item.name;text(name,item.name);
      const progress=document.createElement('td'), percent=item.size?Math.round(100*item.completed/item.size):0;
      text(progress,`${percent}% · ${fmt(item.completed)} / ${fmt(item.size)}`);
      const bar=document.createElement('div'),fill=document.createElement('div');bar.className='progress';fill.style.width=`${Math.min(100,percent)}%`;bar.append(fill);progress.append(bar);
      const status=document.createElement('td');status.className='state';
      if(item.stalled)status.classList.add('stalled');
      const statusLine=document.createElement('div');
      text(statusLine,item.detail||item.status);status.append(statusLine);
      const peers=document.createElement('small');peers.className='peer-info';
      text(peers,`${item.connections ?? 0} connections · ${item.seeders_reported ?? 0} seeders reported`);
      status.append(peers);
      const speed=document.createElement('td');text(speed,`${fmt(item.speed)}/s`);
      const actions=document.createElement('td');
      for(const act of (item.status==='active'?['pause','remove']:item.status==='paused'?['resume','remove']:item.status==='waiting'?['pause','remove']:[])){
        const btn=document.createElement('button');btn.className='tiny';text(btn,act);
        btn.onclick=async()=>{try{await api(`/api/transfers/${encodeURIComponent(item.gid)}/${act}`,{method:'POST'});await transfers();}catch{show('Transfer action failed.',true);}};
        actions.append(btn);
      }
      tr.append(name,progress,status,speed,actions);body.append(tr);
    }
  }catch(e){$('health').textContent='● Offline';show('Unable to load downloader status. Check the container logs.',true);}
}
async function files(path=''){
  try{
    const d=await api(`/api/files?path=${encodeURIComponent(path)}`);cwd=path;
    text($('breadcrumb'),`/downloads${path?'/'+path:''}`);text($('count'),d.items.length);
    $('file-list').replaceChildren();
    if(!d.items.length){const div=document.createElement('div');div.className='empty';div.textContent='This folder is empty.';$('file-list').append(div);}
    for(const f of d.items){
      const row=document.createElement('div');row.className='file';
      const b=document.createElement('button');b.textContent=(f.type==='folder'?'📁 ':'📄 ')+f.name;
      if(f.type==='folder'){b.onclick=()=>files(f.path);}else{b.onclick=()=>window.location.assign(`/api/files/download?path=${encodeURIComponent(f.path)}`);}
      const action=document.createElement('button');action.className='file-action';action.textContent=f.type==='folder'?'Open ↗':`${fmt(f.size)} · Download`;
      action.onclick=b.onclick;row.append(b,action);$('file-list').append(row);
    }
  }catch{show('Could not read file directory.',true);}
}
$('add').onclick=async()=>{
  const uri=$('uri').value.trim();$('add').disabled=true;
  try{await api('/api/transfers',{method:'POST',body:JSON.stringify({uri,tracker_profile:$('discovery-profile').value})});$('uri').value='';show('Transfer queued.');await transfers();}
  catch(e){show(`Unable to queue transfer: ${e.message}`,true);}finally{$('add').disabled=false;}
};
$('add-torrent').onclick=async()=>{
  const file=$('torrent-file').files?.[0];
  if(!file){show('Choose a .torrent file first.',true);return;}
  if(file.size < 1 || file.size > 2*1024*1024){show('Torrent metadata must be under 2 MiB.',true);return;}
  $('add-torrent').disabled=true;
  try {
    await api('/api/transfers/torrent',{
      method:'POST',headers:{'Content-Type':'application/octet-stream','X-MooBee-Discovery-Profile':$('discovery-profile').value},body:file
    });
    $('torrent-file').value='';show('Public torrent queued with selected discovery mode.');
    await transfers();
  }catch(e){show(`Unable to import .torrent: ${e.message}`,true);}
  finally{$('add-torrent').disabled=false;}
};
async function discoveryProfiles(){
  try{
    const d=await api('/api/discovery/profiles');
    const enabled=Boolean(d.curated_enabled);
    for(const opt of $('discovery-profile').options)opt.disabled=opt.value!=='dht'&&!enabled;
    if(!enabled)$('discovery-profile').value='dht';
    text($('discovery-note'),enabled
      ? 'Curated modes are operator-enabled, not proof of network isolation. Ubuntu mode only accepts approved test hashes.'
      : 'Curated trackers are disabled until the operator validates container egress isolation.');
  }catch(e){
    for(const opt of $('discovery-profile').options)opt.disabled=opt.value!=='dht';
    $('discovery-profile').value='dht';
    text($('discovery-note'),'Unable to verify tracker policy; using DHT only.');
  }
}
async function mediaCandidates(){
  const list=$('media-list');
  try{
    const result=await api('/api/media/candidates');
    list.replaceChildren();
    if(!result.items.length){
      const empty=document.createElement('div');
      empty.className='empty';
      empty.textContent='No completed, safely accessible video candidates yet.';
      list.append(empty);
    }
    for(const m of result.items){
      const row=document.createElement('div');
      row.className='file';
      const label=document.createElement('div');
      label.className='media-label';
      const name=document.createElement('strong');
      text(name,m.name);
      const note=document.createElement('small');
      text(note,`Completed · ${fmt(m.size)} · FFprobe required`);
      label.append(name,note);
      const check=document.createElement('button');
      check.className='file-action';
      text(check,'Inspect media');
      check.onclick=async()=>{
        check.disabled=true;
        text($('media-result'),'Inspecting completed media; no upload will occur…');
        try{
          const data=await api('/api/media/inspect',{
            method:'POST',
            body:JSON.stringify({gid:m.gid,index:m.index})
          });
          const v=data.video;
          text($('media-result'),`Verified ${data.name}: ${v.codec}, ${v.width}×${v.height}, ${v.duration_seconds}s. Import not yet connected.`);
        }catch(e){
          text($('media-result'),`Inspection unavailable: ${e.message}`);
        }finally{check.disabled=false;}
      };
      row.append(label,check);
      list.append(row);
    }
  }catch(e){
    list.replaceChildren();
    const msg=document.createElement('div');
    msg.className='empty';
    msg.textContent='Media readiness could not be checked. Verify downloader status.';
    list.append(msg);
  }
}
$('refresh-media').onclick=mediaCandidates;

async function diagnostics(){
  try{
    const d=await api('/api/diagnostics');
    text($('network-state'),`IPv4 DHT: ${d.dht_ipv4?'ON':'OFF'} · IPv6 DHT: ${d.dht_ipv6?'ON':'OFF'} · UDP: ${d.dht_listen_port} · TCP: ${d.peer_listen_port}. ${d.network_status}`);
  }catch(e){text($('network-state'),'Downloader diagnostics unavailable; verify the RPC connection.');}
}
$('check-network').onclick=diagnostics;
$('refresh').onclick=()=>{transfers();diagnostics();};
$('back').onclick=()=>files(cwd.split('/').slice(0,-1).join('/'));
transfers();files();diagnostics();discoveryProfiles();mediaCandidates();setInterval(transfers,5000);
