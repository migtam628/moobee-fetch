const $ = id => document.getElementById(id);
const fmt = n => {let value=Number(n)||0;for(const unit of ['B','KB','MB','GB','TB']){if(value<1024) return `${value.toFixed(value<10&&unit!=='B'?1:0)} ${unit}`;value/=1024;}return `${value.toFixed(1)} PB`;};
let cwd='';
const api=async (path, options={}) => {
  const response=await fetch(path,{...options,headers:{'Content-Type':'application/json',...(options.headers||{})},credentials:'same-origin',cache:'no-store'});
  if(!response.ok) {
    let message=`${response.status} ${response.statusText}`;
    if(response.status === 400 || response.status === 422){
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
      const status=document.createElement('td');status.className='state';text(status,item.status);
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
  try{await api('/api/transfers',{method:'POST',body:JSON.stringify({uri})});$('uri').value='';show('Transfer queued.');await transfers();}
  catch(e){show(`Unable to queue transfer: ${e.message}`,true);}finally{$('add').disabled=false;}
};
$('refresh').onclick=()=>transfers();$('back').onclick=()=>files(cwd.split('/').slice(0,-1).join('/'));
transfers();files();setInterval(transfers,5000);
