import {esc, countryName, relative, fullDate, copyText, getJSON} from './format.js';
import {renderRows, renderSources} from './table.js';
import {openDetail} from './detail.js';

const $ = selector => document.querySelector(selector);
const state = {state:'available',grade:'',retry:'',country:'',protocol:'',project:'connectivity',search:'',sort:'speed',direction:'asc',page:1};
const randomApiUrl = location.origin+'/api/v1/proxies/random';
let busy = false, queued = false, view = 'overview';

const headings = {
  overview:['运行总览','代理池运行总览','查看可用出口、检测队列和服务健康状态。','OPERATIONS OVERVIEW'],
  proxies:['代理节点','节点状态与历史','查看每个出口的通用 HTTPS 连通性和复测记录。','PROXY INVENTORY'],
  sources:['来源监测','从公开来源到有效出口','跟踪来源响应、接收数量和实际验证结果。','SOURCE HEALTH'],
  schedule:['调度设置','服务内部调度','设置来源发现、采集、复测和导出频率。','INTERNAL SCHEDULER'],
  usage:['接入中心','将代理池接入使用端','通过实时 API 领取具体代理地址，或下载导出文件。','INTEGRATION CENTER']
};

function switchView(next) {
  if (!headings[next]) return;
  view = next;
  for (const name of Object.keys(headings)) $(`#${name}-view`).hidden = name !== view;
  document.querySelectorAll('.nav-item').forEach(button => button.classList.toggle('selected',button.dataset.view === view));
  ['#breadcrumb','#page-title','#page-description','#page-kicker'].forEach((id,i) => { $(id).textContent = headings[view][i]; });
}

function renderSchedule(data) {
  const form = $('#schedule-form'), value = data.settings || {};
  for (const name of ['source_interval','mihomo_refresh_interval','history_recheck_interval','recheck_interval','new_recheck_interval','export_interval']) form.elements[name].value = Math.round((value[name] || 0)/60);
  form.elements.discovery_interval.value = Math.round((value.discovery_interval || 0)/3600);
  form.elements.source_review_interval.value = Math.round((value.source_review_interval || 0)/3600);
  form.elements.discovery_limit.value = value.discovery_limit || 20;
  form.elements.source_review_batch.value = value.source_review_batch || 20;
  form.elements.discovery_enabled.checked = value.discovery_enabled === true;
  const names = {idle:'等待',running:'运行中',error:'上次失败',disabled:'已关闭'};
  $('#schedule-tasks').innerHTML = (data.tasks || []).map(task => `<article><div><strong>${esc(task.label)}</strong><small>${names[task.status] || esc(task.status)} · 下次 ${relative(task.next_run)}</small></div><span>${task.error ? esc(task.error) : `上次完成 ${relative(task.last_finished)}`}</span></article>`).join('')+`<p class="candidate-note">待审核新来源：${Number(data.source_candidates?.pending || 0).toLocaleString()} 个。通过质量门槛后自动接入正式采集。</p>`;
  const states = {active:'已自动接入',pending:'等待复核',deferred:'等待能力支持',covered:'已有覆盖',rejected:'已拒绝'};
  const reasons = {quality_gate_passed:'质量门槛通过',repository_already_configured:'地址已配置',no_meaningful_novelty:'没有有效新增',engine_protocol_not_supported:'当前管线暂未支持',download_failed:'下载失败，稍后重试',too_few_records:'有效记录太少',invalid_format:'格式不兼容',awaiting_rediscovery:'等待再次发现',insufficient_live_evidence:'连通证据不足'};
  $('#source-reviews').innerHTML = '<h3>候选来源审核</h3>'+(data.source_reviews || []).map(row => { const r=row.review || {}; const route={direct:'直连池',mihomo:'Mihomo',deferred:'专用管线'}[r.route] || '待识别'; return `<article><div><strong>${esc(row.repository || row.name)}</strong><small>${reasons[r.reason] || '尚未审核'} · ${route} · 发现 ${row.discoveries} 次</small></div><span class="review-state ${esc(row.state)}">${states[row.state] || esc(row.state)}</span><dl><div><dt>有效记录</dt><dd>${Number(r.records || 0).toLocaleString()}</dd></div><div><dt>库存新增</dt><dd>${Number(r.novel || 0).toLocaleString()}</dd></div><div><dt>已知可用</dt><dd>${Number(r.known_available || 0).toLocaleString()}</dd></div><div><dt>抽测通过</dt><dd>${Number(r.sample_available || 0)} / ${Number(r.sampled || 0)}</dd></div></dl></article>`; }).join('');
}

async function loadSchedule() {
  try { renderSchedule(await getJSON('/api/schedule')); }
  catch (error) { setText('#error',error.message); $('#error').hidden = false; }
}

function setText(id, value) { $(id).textContent = value; }
function renderService(status) {
  const now = Date.now()/1000;
  const profile = status.profiles?.connectivity || {};
  const heartbeat = status.heartbeat || {};
  const alive = now - (heartbeat.time || 0) < 180;
  const stopped = (profile.paused_until || 0) > 4100000000;
  const paused = (profile.paused_until || 0) > now;
  const power = $('#power');
  power.textContent = stopped ? '○ 已停止' : '● 运行中';
  power.classList.toggle('secondary', stopped);
  power.title = stopped ? '收集与校验已停止，点击恢复' : '点击停止收集与校验';
  setText('#daemon-state', alive ? '维护服务运行中' : '维护心跳已延迟');
  $('#daemon-dot').classList.toggle('stale',!alive);
  setText('#run-state', stopped ? '收集与检测已停止' : paused ? '连通性检测已暂停' : heartbeat.state === 'staged_pipeline' ? '正在进行最小 HTTPS 连通性检测' : heartbeat.state === 'pipeline' ? '预筛与 HTTPS 检测同时进行' : heartbeat.state === 'screening' ? '正在快速预筛' : '等待下一轮复测');
  return {stopped,paused,profile};
}

function renderOverview(data) {
  const {status,states,config} = data;
  const available = states.available || 0;
  const connectivity = status.connectivity || {};
  const profile = status.profiles?.connectivity || {};
  const queues = data.scheduling || {};
  const last = status.last_cycle || {};
  const plan = status.pipeline?.plan || last.pipeline || {};
  const service = renderService(status);
  setText('#available-count',available.toLocaleString());
  setText('#unique-exits',Number(profile.unique_exit_ips || 0).toLocaleString());
  setText('#available-split',available ? `已通过最小 HTTPS 转发验证 · 国内站命中 ${Number(connectivity.domestic || 0).toLocaleString()} · 国外站命中 ${Number(connectivity.overseas || 0).toLocaleString()}` : '通过通用 HTTPS 连通性检测');
  setText('#tab-available',available);
  setText('#candidate-count',Number(status.candidates || 0).toLocaleString());
  setText('#pending-count',Number(states.untested || 0).toLocaleString());
  setText('#checked-count',Math.max(0,(status.candidates || 0)-(states.untested || 0)).toLocaleString());
  setText('#median',data.median_ms == null ? '—' : Number(data.median_ms).toLocaleString()+' ms');
  setText('#domestic-count',Number(connectivity.domestic || 0).toLocaleString());
  setText('#overseas-count',Number(connectivity.overseas || 0).toLocaleString());
  $('#domestic-bar').style.width = `${Math.min(100,(connectivity.domestic || 0)/Math.max(1,available)*100)}%`;
  $('#overseas-bar').style.width = `${Math.min(100,(connectivity.overseas || 0)/Math.max(1,available)*100)}%`;
  setText('#cycle-text',service.stopped ? '点击右上角开关恢复' : service.paused ? '恢复时间：'+fullDate(service.profile.paused_until) : `上轮 ${last.tested ?? 0} 个 · 预筛拦截 ${last.prefilter_failed ?? 0} · ${relative(last.finished_at)}`);
  setText('#cadence',`${config.prefilter_workers} 路预筛 / ${plan.probe_workers || config.workers} 路最小 HTTPS 检测 · ${plan.launch_interval ?? config.probe_interval} 秒发起间隔 · ${plan.reason || '等待调度'}`);
  [['#due-count','due'],['#cooling-count','cooling'],['#recovery-count','recovery'],['#probe-count','probes_10m']].forEach(([id,key]) => setText(id,Number(queues[key] || 0).toLocaleString()));
  setText('#queue-summary',`冷队列 ${Number(queues.cold || 0).toLocaleString()} · 历史可用优先恢复`);
  const sources = status.sources || [];
  const healthySources = sources.filter(source => source.http_status === 200 && !source.error).length;
  setText('#source-count',`${healthySources} / ${sources.length}`);
  const ports = status.clash_exit?.ports || [];
  setText('#instance-mihomo',ports.length > 1 ? `${ports[0]}–${ports[1]}` : ports[0] || '—');
  setText('#overview-updated',new Date().toLocaleTimeString('zh-CN',{hour12:false}));
  setText('#console-version','v'+(status.version || '—'));
  const country = state.country;
  $('#country').innerHTML = '<option value="">所有国家 / 地区</option>'+(data.countries || []).map(c => `<option value="${esc(c.code || 'unknown')}">${esc(countryName(c.code))} · ${c.count.toLocaleString()}</option>`).join('');
  $('#country').value = country;
  const gradeNames = {A:'稳定',B:'观察',C:'保留',D:'待检/复核',E:'失败重试'};
  setText('#grade-summary',Object.entries(gradeNames).map(([key,name]) => `${key} ${name} ${data.grades?.[key] || 0}`).join(' · ')+` ｜ 长期失败归档 ${status.retirement?.archived || 0} 个`);
  renderSources(status);
}

async function refresh() {
  if (busy) { queued = true; return; }
  busy = true; $('#refresh').disabled = true;
  try {
    const [overview,list] = await Promise.all([getJSON('/api/overview'),getJSON('/api/proxies?'+new URLSearchParams(state))]);
    if (!queued) {
      renderOverview(overview); renderRows(list); state.page = list.page;
      setText('#updated','数据同步于 '+new Date().toLocaleTimeString('zh-CN',{hour12:false}));
      $('#error').hidden = true;
    }
  } catch (error) { setText('#error',error.message+'，保留上次数据。'); $('#error').hidden = false; }
  finally { busy = false; $('#refresh').disabled = false; if (queued) { queued = false; refresh(); } }
}

function setState(value) {
  state.state = value; state.page = 1; switchView('proxies');
  document.querySelectorAll('.state-tabs button').forEach(button => button.classList.toggle('active',button.dataset.state === value));
  refresh();
}

document.querySelectorAll('[data-state]').forEach(button => { button.onclick = () => setState(button.dataset.state); });
document.querySelectorAll('[data-view]').forEach(button => { button.onclick = () => { switchView(button.dataset.view); if (button.dataset.view === 'schedule') loadSchedule(); }; });
for (const key of ['grade','retry','country','protocol','project']) $(`#${key}`).onchange = event => { state[key] = event.target.value; state.page = 1; refresh(); };
let debounce;
$('#search').oninput = event => { clearTimeout(debounce); debounce = setTimeout(() => { state.search = event.target.value; state.page = 1; refresh(); },250); };
document.querySelectorAll('[data-sort]').forEach(button => { button.onclick = () => {
  state.direction = state.sort === button.dataset.sort && state.direction === 'asc' ? 'desc' : 'asc'; state.sort = button.dataset.sort; state.page = 1;
  document.querySelectorAll('[data-sort]').forEach(other => other.parentElement.removeAttribute('aria-sort'));
  button.parentElement.setAttribute('aria-sort',state.direction === 'asc' ? 'ascending' : 'descending'); refresh();
}; });
$('#prev').onclick = () => { state.page = Math.max(1,state.page-1); refresh(); };
$('#next').onclick = () => { state.page += 1; refresh(); };
$('#refresh').onclick = refresh;
$('#power').onclick = async () => {
  const data = await getJSON('/api/overview');
  const paused = (data.status.profiles?.connectivity?.paused_until || 0) > Date.now()/1000;
  $('#power').disabled = true;
  try {
    const response = await fetch('/api/power',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({on:paused})});
    if (!response.ok) throw new Error(`操作失败（${response.status}）`);
  } catch (error) { setText('#error',error.message); $('#error').hidden = false; }
  finally { $('#power').disabled = false; refresh(); }
};
$('#schedule-form').onsubmit = async event => {
  event.preventDefault(); const form = event.currentTarget; const button = form.querySelector('button');
  const minutes = name => Math.round(Number(form.elements[name].value)*60);
  const data = {source_interval:minutes('source_interval'),discovery_interval:Math.round(Number(form.elements.discovery_interval.value)*3600),discovery_limit:Math.round(Number(form.elements.discovery_limit.value)),source_review_interval:Math.round(Number(form.elements.source_review_interval.value)*3600),source_review_batch:Math.round(Number(form.elements.source_review_batch.value)),mihomo_refresh_interval:minutes('mihomo_refresh_interval'),discovery_enabled:form.elements.discovery_enabled.checked,history_recheck_interval:minutes('history_recheck_interval'),recheck_interval:minutes('recheck_interval'),new_recheck_interval:minutes('new_recheck_interval'),export_interval:minutes('export_interval')};
  button.disabled = true;
  try { const response = await fetch('/api/schedule',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)}); const value = await response.json(); if (!response.ok) throw new Error(value.error || `保存失败（${response.status}）`); renderSchedule(value); setText('#schedule-saved','已保存 · '+new Date().toLocaleTimeString('zh-CN',{hour12:false})); }
  catch (error) { setText('#error',error.message); $('#error').hidden = false; }
  finally { button.disabled = false; }
};
$('#rows').onclick = event => { const button = event.target.closest('[data-proxy]'); if (button) openDetail(button.dataset.proxy,state.project); };
document.querySelectorAll('[data-copy-path]').forEach(button => { button.onclick = () => copyText(location.origin+button.dataset.copyPath); });
$('#copy-api').onclick = () => copyText(randomApiUrl);
setText('#api-url',randomApiUrl);
setText('#instance-api',location.origin);
setText('#service-host',location.host); setText('#next-url',randomApiUrl);
setText('#clash-url',location.origin+'/connectivity/clash.yaml');
switchView('overview');
setInterval(() => { if ($('#auto').checked && !document.hidden) refresh(); },10000);
refresh();
