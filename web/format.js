export const names = {available:'可用',unreachable:'连接失败',expired:'已过期',unverified:'待复核',untested:'未检测',disabled:'已停用',auth_required:'需认证，不可用'};
export const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
export const fullDate = value => value ? new Date(value * 1000).toLocaleString('zh-CN', {hour12:false}) : '未记录';
export function relative(value) {
  if (!value) return '未记录';
  const seconds = Math.round(Date.now() / 1000 - value), n = Math.abs(seconds);
  const text = n < 60 ? `${n} 秒` : n < 3600 ? `${Math.floor(n/60)} 分钟` : n < 86400 ? `${Math.floor(n/3600)} 小时` : `${Math.floor(n/86400)} 天`;
  return text + (seconds >= 0 ? '前' : '后');
}
export function countryName(code) {
  if (!code || code === 'unknown') return '未知地区';
  try { return new Intl.DisplayNames(['zh-CN'], {type:'region'}).of(code.toUpperCase()); }
  catch { return code; }
}
export const badge = state => `<span class="badge ${Object.hasOwn(names,state) ? state : 'unverified'}">${esc(names[state] || state)}</span>`;
let timer;
export function toast(message) {
  const node = document.querySelector('#toast');
  node.textContent = message; node.hidden = false;
  clearTimeout(timer); timer = setTimeout(() => { node.hidden = true; }, 2500);
}
export async function copyText(text) {
  try { await navigator.clipboard.writeText(text); toast('已复制'); }
  catch { toast('复制失败，请手动选择地址复制'); }
}
export async function getJSON(url) {
  const response = await fetch(url, {cache:'no-store', signal:AbortSignal.timeout(15000)});
  if (!response.ok) throw new Error(`数据请求失败（${response.status}）`);
  return response.json();
}
