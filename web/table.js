import {esc, badge, relative, fullDate, countryName} from './format.js';
const timeCell = value => `<td class="time-cell" title="${esc(fullDate(value))}">${value ? esc(relative(value)) : '—'}<small>${value ? esc(new Date(value*1000).toLocaleTimeString('zh-CN',{hour12:false})) : '未记录'}</small></td>`;
export function renderRows(data) {
  document.querySelector('#rows').innerHTML = data.items.length ? data.items.map(row => {
    const speed = row.latency_ms;
    return `<tr><td><div class="address">${esc(row.host)}:${esc(row.port)}</div>${row.exit_ip ? `<span class="exit-ip">出口 ${esc(row.exit_ip)}</span>` : ''}</td>
      <td><span class="tier tier-${esc(row.grade)}">${esc(row.grade)} · ${esc(row.grade_name)}</span><small class="subtle">${row.grade === 'A' || row.grade === 'B' ? '稳定计分 '+Number(row.success_streak)+'/3' : esc(row.retry_name || '失败重试')}</small></td>
      <td><span class="protocol ${row.protocol === 'SOCKS5' ? 'socks5' : ''}">${esc(row.protocol)}</span></td><td>${esc(countryName(row.country || 'unknown'))}</td>
      <td><span class="project-label"><span class="project-dot"></span>${row.domestic ? '国内站✓' : '国内站—'} · ${row.overseas ? '国外站✓' : '国外站—'}</span><small class="subtle">任一 HTTPS 成功即通过</small></td>
      <td><div class="speed ${speed > 5000 ? 'slow' : ''}">${speed == null ? '—' : Math.round(speed).toLocaleString()+' <small>ms</small>'}${speed == null ? '' : `<progress value="${Math.min(12000,speed)}" max="12000"></progress>`}</div></td>
      <td>${badge(row.effective_state)}</td>${timeCell(row.checked_at)}${timeCell(row.last_success)}${timeCell(row.last_failure)}
      <td><button class="detail-button" data-proxy="${esc(row.url)}" aria-label="查看 ${esc(row.host)} 详情">↗</button></td></tr>`;
  }).join('') : '<tr><td colspan="11" class="empty">没有符合条件的代理。可切换到「全部」查看检测进度。</td></tr>';
  const pages = Math.max(1,Math.ceil(data.total/data.page_size));
  document.querySelector('#result-count').textContent = `${data.total.toLocaleString()} 个符合条件的节点`;
  document.querySelector('#page-label').textContent = data.total ? `显示 ${(data.page-1)*data.page_size+1}–${Math.min(data.page*data.page_size,data.total)} / 共 ${data.total.toLocaleString()} 个` : '共 0 个';
  document.querySelector('#page-number').textContent = `${data.page} / ${pages}`;
  document.querySelector('#prev').disabled = data.page <= 1;
  document.querySelector('#next').disabled = data.page >= pages;
}
export function renderSources(status) {
  document.querySelector('#source-updated').textContent = '最近拉取：'+relative(status.last_collection);
  const summary = status.source_summary || {};
  const sources = [...(status.sources || [])].sort((a,b) => Number(b.available || 0)-Number(a.available || 0) || Number(b.stored_candidates || 0)-Number(a.stored_candidates || 0));
  document.querySelector('#source-total').textContent = `${Number(summary.total || sources.length).toLocaleString()} 个`;
  document.querySelector('#source-summary').textContent = `健康 ${Number(summary.healthy || 0)} 个 · 正在贡献可用节点 ${Number(summary.contributing || 0)} 个`;
  const exit = status.clash_exit || {};
  const exitCard = exit.lanes ? `<article class="source-card"><header><h3>clash-exit 本机免费出口</h3>${badge(exit.alive > 0 ? 'available' : 'unreachable')}</header>
      <p><span class="source-url">mihomo 每节点一端口 · 127.0.0.1:${esc(String(exit.ports[0] || ''))}+</span></p>
      <p>端口总数 ${Number(exit.lanes || 0).toLocaleString()} · 自测存活 ${Number(exit.alive || 0)} · 轮换储备 ${Number(exit.reserve || 0)}</p>
      <p>存活中位延迟 ${exit.median_ms == null ? '—' : Number(exit.median_ms).toLocaleString()+' ms'}</p>
      <footer><span>clash-free-http 的上游</span><span>${exit.refreshed_at ? '刷新于 '+esc(relative(exit.refreshed_at)) : ''}</span></footer></article>` : '';
  document.querySelector('#source-list').innerHTML = exitCard + (sources.length ? sources.map(source => {
    const healthy = source.http_status === 200 && !source.error;
    const state = healthy ? 'available' : 'unreachable';
    return `<article class="source-card"><header><h3>${esc(source.name || '代理来源')}</h3>${badge(state)}</header>
      <p><a class="source-url" href="${esc(source.fetch_url || source.url || '')}" target="_blank" rel="noopener noreferrer">${esc(source.fetch_url || source.url || '')}</a></p><p>本次接收 ${Number(source.accepted || 0).toLocaleString()} · 新增 ${Number(source.new_candidates || 0).toLocaleString()} · 待准入 ${Number(source.queued_candidates || 0).toLocaleString()}</p>
      <p class="contribution">当前归属 ${Number(source.stored_candidates || 0).toLocaleString()} · 可用贡献 ${Number(source.available || 0).toLocaleString()} · 覆盖可用池 ${Number(source.contribution_percent || 0).toFixed(1)}%</p>
      <p>24小时验证成功 ${Number(source.verified_24h || 0).toLocaleString()}</p>
      <footer><span>${source.deferred ? '等待刷新' : esc(source.error || 'HTTP '+source.http_status)}</span><span>${source.next_fetch ? '下次 '+esc(relative(source.next_fetch)) : ''}</span></footer></article>`;
  }).join('') : '<p class="empty">尚无来源拉取记录</p>');
}
