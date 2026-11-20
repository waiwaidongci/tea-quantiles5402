import {points,median} from '../api/research.js';
import {table} from '../components/common/table.js';
import {status} from '../components/common/status.js';
import {esc,fmt} from '../utils/format.js';
import {bindForm} from '../hooks/forms.js';

let watching=false;
const writable=()=>localStorage.getItem('research-role')!=='viewer';

function createForm(pts){
  if(!writable())return '<p class="method-note">观察员可查看合批研究与进度；发起研究需要分析师或负责人。</p>';
  if(!pts.length)return '<p class="method-note">尚无在线注册检验点，请等待检验点心跳上报。</p>';
  return `<form id="median-form" class="worksheet"><h2>发起合批研究</h2>
<p>开算即锁定各检验点的当前版本与份数；总台只逐轮询问「不超过阈值的份数」，每点最多 30 个阈值，求合批精确中位数（偶数取中间两值的平均）。</p>
<label>研究名称<input name="name" required minlength="2" value="合批含水率中位数 · ${new Date().toISOString().slice(0,10)}"></label>
<fieldset class="point-picks"><legend>选择合批检验点（当前版本将被锁定）</legend>
${pts.map(p=>`<label class="point-pick"><input type="checkbox" name="point_${p.id}" checked><span>${esc(p.name)} <small>${esc(p.code)} · v${p.version} · ${p.sample_count}份 · ${p.online?'在线':'掉线中'}</small></span></label>`).join('')}
</fieldset>
<button type="submit">锁定版本并开始合批 →</button></form>`;
}

function listView(pts,runs){
  return `<div class="eyebrow">MEDIAN / 合批研究</div><h1>合批精确中位数</h1>
<p class="lead">茶样含水率原值留在各检验点，总台只问阈值以下（含等值）的份数，按百分之一百分点记值逐轮二分，收齐原版回执才推进。</p>
${createForm(pts)}
<h2>研究记录 <small>进入详情页可观看等待到出数全过程</small></h2>
${table(['编号 / 研究','状态','检验点','合批份数','中位数 (%)','发起时间 (UTC)'],runs.map(r=>[
  {html:`<a href="#median/${r.id}">#${r.id} ${esc(r.name)}</a>`},
  {html:status(r.status)},r.point_count,r.total_count,
  r.median==null?'—':fmt(r.median),r.created_at]))}`;
}

function resultBlock(r){
  const res=r.result;
  const ties=res.median_frequency.map(f=>`<tr><td>${fmt(f.value)}</td><td>${f.points_sharing}</td><td>${f.total}</td><td>${Object.entries(f.per_point).map(([c,n])=>`${esc(c)}: ${n}`).join('；')}</td></tr>`).join('');
  return `<section class="result-intro"><strong>合批精确中位数 ${fmt(res.median)}%</strong>
<p>${esc(res.interpretation)}</p><p>${esc(res.caveat)}</p></section>
<h2>核心数值</h2>
${table(['指标','值'],[['合批份数',res.total_count],['中位次（1起始）',res.ranks.join('、')],['中间值 (%)',res.middle_values.join('、')],['精确中位数 (%)',res.median],['记值精度 (%)',res.grid_step],['询问轮次',res.rounds_used],['每点阈值预算',res.threshold_budget],['回执台账 SHA256',{html:`<span class="checksum">${esc(res.ledger_sha256)}</span>`}]])}
<h2>跨点同值核对 <small>各检验点取值恰为中位值的份数</small></h2>
<div class="table-wrap"><table><thead><tr><th>中位值 (%)</th><th>出现点数</th><th>合计份数</th><th>分点份数</th></tr></thead><tbody>${ties}</tbody></table></div>
<h2>各点询问明细 <small>每点阈值数不超过30</small></h2>
${table(['检验点','锁定版本','份数','已问阈值','阈值列表 (%)'],res.points.map(p=>[p.name,'v'+p.version,p.count,`${p.asked_count} / ${res.threshold_budget}`,{html:`<small>${p.thresholds.join(', ')}</small>`}]))}`;
}

function detailView(r,currentPoints){
  const p=r.progress;
  const current=Object.fromEntries((currentPoints||[]).map(x=>[x.code,x]));
  const pointsRows=r.points.map(lp=>{
    const cur=current[lp.code];
    const drift=cur&&cur.version!==lp.version?` <small class="stale-text">现已 v${cur.version}</small>`:'';
    return [lp.name,'v'+lp.version,lp.count,`${p.asked_count[lp.code]||0} / 30`,p.received_count[lp.code]||0,
      {html:(p.awaiting||[]).includes(lp.code)?'<span class="status queued">等待回执</span>':'<span class="status completed">已齐</span>'},{html:drift||'—'}];
  });
  const rounds=(p.rounds||[]).map((rd,i)=>[i+1,fmt(rd.threshold),`${rd.received} / ${rd.of}`,rd.cum==null?{html:'<span class="status queued">等待中</span>'}:rd.cum]);
  let banner='';
  if(r.status==='stale')banner=`<div class="error">锁定版本已失效：${esc(r.error)}</div>`;
  if(r.status==='failed')banner=`<div class="error">计算失败：${esc(r.error)}</div>`;
  if(r.status==='queued'||r.status==='collecting')banner=`<p class="method-note">正在等待各检验点回执${(p.awaiting||[]).length?`（等待：${p.awaiting.map(esc).join('、')}）`:''}。此页每2秒更新，收齐原版回执即推进。</p>`;
  return `<div class="eyebrow">MEDIAN / RUN ${r.id}</div><h1>${esc(r.name)} ${status(r.status)}</h1>
<div class="checksum">LOCK SHA256 ${r.checksum}</div>
<p>锁定 ${r.points.length} 个检验点 · 合批 ${r.total_count} 份 · 中位次 ${r.ranks.join('、')} · ${esc(r.created_at)}</p>
${banner}
${r.status==='completed'&&r.result?resultBlock(r):''}
<h2>锁定版本与回执进度</h2>
${table(['检验点','锁定版本','份数','已问阈值','已收回执','状态','此后变动'],pointsRows)}
<h2>询问轮次 <small>每轮向各点询问同一阈值以下（含等值）份数</small></h2>
${rounds.length?table(['轮次','阈值 (%)','回执','累计不超过份数'],rounds):'<p>尚未发出询问。</p>'}`;
}

export async function render(id){
  if(!id){
    watching=false;
    const [pts,runs]=await Promise.all([points.list(),median.list()]);
    return listView(pts,runs);
  }
  const [r,pts]=await Promise.all([median.get(id),points.list()]);
  watching=['queued','collecting'].includes(r.status);
  return detailView(r,pts);
}

export function bind(refresh){
  bindForm('median-form',async data=>{
    const ids=Object.keys(data).filter(k=>k.startsWith('point_')).map(k=>+k.slice(6));
    if(!ids.length)throw new Error('至少选择一个检验点');
    const r=await median.create({name:data.name,point_ids:ids});
    location.hash='median/'+r.id;
  });
}

export async function poll(id,refresh){
  if(location.hash.startsWith('#median/')&&watching)await refresh();
}
