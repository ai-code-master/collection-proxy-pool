import {esc, badge, fullDate, countryName, getJSON, copyText} from './format.js';
const dialog = document.querySelector('#detail');
dialog.setAttribute('aria-labelledby','detail-title');
document.querySelector('#close-detail').onclick = () => dialog.close();
dialog.addEventListener('click', event => { if (event.target === dialog && event.clientX < dialog.getBoundingClientRect().left) dialog.close(); });
let version = 0;
dialog.addEventListener('close', () => {
  if (dialog.open) return;
  ++version;
  document.querySelector('#detail-body').replaceChildren();
});
export async function openDetail(url, project) {
  const request = ++version;
  document.querySelector('#detail-title').textContent = url;
  const body = document.querySelector('#detail-body');
  body.textContent = '正在读取检测历史…';
  if (!dialog.open) dialog.showModal();
  try {
    const data = await getJSON('/api/history?'+new URLSearchParams({proxy:url,project}));
    if (request !== version || !dialog.open) return;
    const row = data.proxy;
    const exitCountry = row.country && row.country !== 'unknown' ? `${countryName(row.country)}（${row.country.toUpperCase()}）` : '未识别';
    const fields = [['调度等级',row.grade+' · '+row.grade_name],['复测队列',row.retry_name],['稳定计分',row.success_streak+'/3（两次计分至少间隔 5 分钟）'],['协议',row.protocol],['检测类型',row.project],['国内站命中',row.domestic ? '已确认 · '+Math.round(row.domestic_latency_ms)+' ms' : '未确认'],['国外站命中',row.overseas ? '已确认 · '+Math.round(row.overseas_latency_ms)+' ms' : '未确认'],['出口国家 / 地区',exitCountry],['出口 IP',row.exit_ip || '未识别'],['首次收集',fullDate(row.first_seen)],['最近收集',fullDate(row.last_seen)],['最近检测',fullDate(row.checked_at)],['计划复测',fullDate(row.next_check)],['有效截止',fullDate(row.valid_until)],['HTTP 状态',row.http_status ?? '未记录']];
    const events = data.events, max = Math.max(1,...events.map(e => e.latency_ms || 0));
    const bars = [...events].reverse().map((e,i) => {
      const height = Math.max(4,(e.latency_ms || 0)/max*70);
      return `<rect class="${e.state === 'available' ? 'ok' : 'bad'}" x="${i*12+4}" y="${80-height}" width="8" height="${height}"><title>${esc(fullDate(e.checked_at))} · ${esc(e.state)} · ${Math.round(e.latency_ms || 0)} ms</title></rect>`;
    }).join('');
    body.innerHTML = `<div>${badge(row.effective_state)} <button class="copy-node">复制代理地址</button></div><dl class="detail-grid">${fields.map(([key,value]) => `<div><dt>${esc(key)}</dt><dd>${esc(value)}</dd></div>`).join('')}</dl>
      <section class="detail-section"><h3>最近检测说明</h3><p>${esc(row.reason || '尚未检测')}</p></section>
      <section class="detail-section"><h3>响应记录 <small>最近 ${events.length} 次</small></h3><p>条高表示请求耗时，绿色为校验成功。失败耗时不代表可用速度。</p><div class="history-graph"><svg viewBox="0 0 488 90" role="img" aria-label="最近检测耗时，绿色成功，红色失败">${bars}</svg></div></section>
      <section class="detail-section"><h3>通断检测时间线</h3><p>保留 14 天记录。</p>${events.length ? events.map(e => `<div class="event"><div class="event-time">${esc(fullDate(e.checked_at))}</div><div>${badge(e.state)} <span class="event-speed">${Math.round(e.latency_ms || 0)} ms · HTTP ${esc(e.http_status ?? '—')}</span></div><p class="reason">${esc(e.reason)}</p></div>`).join('') : '<p>暂无检测记录</p>'}</section>
      <section class="detail-section"><h3>收集来源</h3>${row.sources.map(source => `<p>${esc(source)}</p>`).join('')}</section>`;
    body.querySelector('.copy-node').onclick = () => copyText(url);
  } catch (error) { if (request === version) body.textContent = error.message; }
}
