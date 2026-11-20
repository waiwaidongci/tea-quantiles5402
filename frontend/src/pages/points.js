import {points} from '../api/research.js';
import {table} from '../components/common/table.js';
import {esc} from '../utils/format.js';
import {bindForm} from '../hooks/forms.js';

const KIND_LABELS={register:'注册',update:'在线更新',offline_update:'掉线期间更新',online:'恢复连接'};
const writable=()=>localStorage.getItem('research-role')!=='viewer';

export async function render(){
  const [pts,events]=await Promise.all([points.list(),points.events()]);
  const can=writable();
  return `<div id="points-page"><div class="eyebrow">POINTS / 检验点连线</div><h1>检验点</h1>
<p class="lead">含水率原值留在各检验点本地，总台只询问「不超过指定阈值的份数」。此页核对各点版本、份数与连接状态，以及掉线期间发生的数据更新。</p>
${table(['检验点','连接','当前版本','份数','最近心跳 (UTC)',...(can?['操作']:[])],pts.map(p=>[
  {html:`<b>${esc(p.name)}</b> <small>${esc(p.code)}</small>`},
  {html:p.online?'<span class="status completed">在线</span>':'<span class="status stale">掉线</span>'},
  'v'+p.version,p.sample_count,p.last_seen_at,
  ...(can?[{html:`<button class="text" data-toggle="${p.id}" data-online="${p.online?0:1}">${p.online?'模拟掉线':'恢复连接'}</button>`}]:[])]))}
${can?`<details id="update-details"><summary>下发数据更新（模拟点侧数据变更；指令入队，点在线后执行）</summary><form id="point-update-form" class="inline-form"><label>检验点<select name="point_id">${pts.map(p=>`<option value="${p.id}">${esc(p.name)} · v${p.version}</option>`).join('')}</select></label><label>方式<select name="mode"><option value="regenerate">按分布重新生成</option><option value="set">录入给定数值</option></select></label><label>份数 n<input name="n" type="number" value="100" min="1" max="5000"></label><label>数值（逗号分隔，0.01精度）<input name="values" placeholder="如 5.21, 5.23, 5.23"></label><button type="submit">下发到检验点队列</button></form></details>`:'<p class="method-note">观察员可查看检验点状态与事件；下发更新与模拟掉线需要分析师或负责人。</p>'}
<h2>版本与连接事件 <small>「掉线期间更新」= 心跳中断期间发生的版本变化</small></h2>
${table(['时间 (UTC)','检验点','事件','说明'],events.map(e=>[e.created_at,{html:`${esc(e.point_name)} <small>${esc(e.point_code)}</small>`},{html:e.kind==='offline_update'?`<b class="stale-text">${KIND_LABELS[e.kind]||e.kind}</b>`:(KIND_LABELS[e.kind]||e.kind)},e.detail]))}
</div>`;
}

export function bind(refresh){
  document.querySelectorAll('[data-toggle]').forEach(el=>el.onclick=async()=>{
    try{await points.online(+el.dataset.toggle,el.dataset.online==='1');await refresh();}
    catch(e){document.getElementById('notice').textContent=e.message;}
  });
  bindForm('point-update-form',async data=>{
    const id=+data.point_id;
    const body=data.mode==='set'
      ?{mode:'set',values:String(data.values||'').split(/[,\s，、]+/).filter(Boolean).map(Number)}
      :{mode:'regenerate',n:+data.n||100};
    if(body.values&&(body.values.length===0||body.values.some(v=>!Number.isFinite(v))))throw new Error('数值列表为空或含非数字');
    const r=await points.update(id,body);
    document.getElementById('notice').textContent=`已下发：${r.note}`;
  });
}

export async function poll(id,refresh){
  if(location.hash!=='#points')return;
  if(document.querySelector('#points-page details[open]'))return;  // 不打断正在填写更新表单
  await refresh();
}
