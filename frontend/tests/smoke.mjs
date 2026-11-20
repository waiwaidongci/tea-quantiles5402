// 前端页面渲染冒烟测试：jsdom + 模拟 API，验证合批/检验点页面真实渲染。
import {JSDOM} from 'jsdom';

const dom = new JSDOM('<!doctype html><html><body><div id="app"></div><div id="notice"></div></body></html>',
  {url: 'http://localhost:18240/'});
globalThis.window = dom.window;
globalThis.document = dom.window.document;
globalThis.location = dom.window.location;
globalThis.localStorage = dom.window.localStorage;
globalThis.FormData = dom.window.FormData;
localStorage.setItem('research-token', 't');
localStorage.setItem('research-role', 'analyst');

const POINTS = [
  {id: 1, code: 'PT-EAST', name: '城东检验点', version: 3, sample_count: 120, last_seen_at: '2026-11-20T09:00:00+00:00', online: true},
  {id: 2, code: 'PT-NORTH', name: '山口检验点', version: 2, sample_count: 96, last_seen_at: '2026-11-20T08:59:00+00:00', online: false},
];
const EVENTS = [
  {id: 3, point_code: 'PT-NORTH', point_name: '山口检验点', kind: 'offline_update', detail: '掉线期间更新：v1→v2，96份', version: 2, sample_count: 96, created_at: '2026-11-20T09:01:00+00:00'},
  {id: 1, point_code: 'PT-EAST', point_name: '城东检验点', kind: 'register', detail: '检验点注册，v1，120份', version: 1, sample_count: 120, created_at: '2026-11-20T08:00:00+00:00'},
];
const RUN_LIST = [
  {id: 7, name: '秋季合批中位数', status: 'completed', points: [], ranks: [216, 217], total_count: 432,
   result: {median: 5.75}, error: '', checksum: 'ab'.repeat(32), created_at: '2026-11-20T09:05:00+00:00',
   updated_at: '2026-11-20T09:06:00+00:00', median: 5.75, point_count: 2},
  {id: 8, name: '等待演示', status: 'collecting', points: [], ranks: [3], total_count: 5,
   result: null, error: '', checksum: 'cd'.repeat(32), created_at: '2026-11-20T09:10:00+00:00',
   updated_at: '2026-11-20T09:10:00+00:00', median: null, point_count: 2},
];
const RUN_DETAIL = {
  id: 7, name: '秋季合批中位数', status: 'completed', ranks: [216, 217], total_count: 432,
  points: [
    {id: 1, code: 'PT-EAST', name: '城东检验点', version: 3, count: 120},
    {id: 2, code: 'PT-NORTH', name: '山口检验点', version: 2, count: 96},
  ],
  result: {
    median: 5.75, middle_values: [5.75, 5.75], ranks: [216, 217], total_count: 432,
    grid_step: 0.01, threshold_budget: 30, rounds_used: 15,
    points: [
      {code: 'PT-EAST', name: '城东检验点', version: 3, count: 120, asked_count: 15, thresholds: [2.5, 5, 5.75]},
      {code: 'PT-NORTH', name: '山口检验点', version: 2, count: 96, asked_count: 15, thresholds: [2.5, 5, 5.75]},
    ],
    median_frequency: [{value: 5.75, per_point: {'PT-EAST': 4, 'PT-NORTH': 3}, total: 7, points_sharing: 2}],
    ledger_sha256: 'ef'.repeat(32),
    interpretation: '合批共432份茶样（偶数），中间两位为第216位的 5.75% 与第217位的 5.75%，取平均得中位数 5.75%。',
    caveat: '原值始终留在各检验点。',
  },
  error: '', checksum: 'ab'.repeat(32), created_at: '2026-11-20T09:05:00+00:00', updated_at: '2026-11-20T09:06:00+00:00',
  progress: {
    asked_count: {'PT-EAST': 15, 'PT-NORTH': 15},
    received_count: {'PT-EAST': 15, 'PT-NORTH': 15},
    awaiting: [],
    rounds: [{threshold: 5.75, received: 2, of: 2, cum: 219}],
  },
};
const RUN_WAITING = {
  ...RUN_DETAIL, id: 8, name: '等待演示', status: 'collecting', result: null,
  progress: {
    asked_count: {'PT-EAST': 3, 'PT-NORTH': 3},
    received_count: {'PT-EAST': 3, 'PT-NORTH': 2},
    awaiting: ['PT-NORTH'],
    rounds: [{threshold: 50, received: 2, of: 2, cum: 400}, {threshold: 25, received: 1, of: 2, cum: null}],
  },
};

globalThis.fetch = async (url, options = {}) => {
  const ok = (body) => ({ok: true, status: 200, headers: {get: () => 'application/json'}, json: async () => body});
  const u = String(url);
  if (u.endsWith('/points/events')) return ok(EVENTS);
  if (u.endsWith('/points')) return ok(POINTS);
  if (u.endsWith('/median-runs')) {
    if (options.method === 'POST') return ok({...RUN_LIST[1], id: 9});
    return ok(RUN_LIST);
  }
  if (u.endsWith('/median-runs/7')) return ok(RUN_DETAIL);
  if (u.endsWith('/median-runs/8')) return ok(RUN_WAITING);
  if (u.endsWith('/studies')) return ok([]);
  if (u.endsWith('/runs')) return ok([]);
  throw new Error('未模拟的接口: ' + u);
};

const {pages} = await import(new URL('../src/router/index.js', import.meta.url).href);
let failures = 0;
const check = (label, cond) => {
  console.log((cond ? 'PASS' : 'FAIL') + ' ' + label);
  if (!cond) failures++;
};

// 合批列表页：发起表单 + 记录表
const listHtml = await pages.median.render();
check('合批页含发起表单', listHtml.includes('发起合批研究'));
check('合批页含检验点勾选与版本', listHtml.includes('PT-EAST') && listHtml.includes('v3'));
check('合批页说明阈值预算30', listHtml.includes('30 个阈值'));
check('合批页记录含中位数', listHtml.includes('5.75'));
check('合批页含等待状态', listHtml.includes('回收回执中'));

// 合批详情页（已完成）：出数 + 跨点同值
const doneHtml = await pages.median.render(7);
check('详情页含精确中位数', doneHtml.includes('合批精确中位数'));
check('详情页含跨点同值核对', doneHtml.includes('跨点同值核对') && doneHtml.includes('PT-EAST: 4'));
check('详情页含台账SHA256', doneHtml.includes('ef'.repeat(32)));
check('详情页含阈值列表', doneHtml.includes('2.5, 5, 5.75'));
check('详情页含轮次表', doneHtml.includes('询问轮次'));

// 合批详情页（等待中）：等待提示 + 未齐轮次
const waitHtml = await pages.median.render(8);
check('等待页提示等待回执', waitHtml.includes('正在等待各检验点回执') && waitHtml.includes('PT-NORTH'));
check('等待页含等待中轮次', waitHtml.includes('等待中'));
check('等待页含每2秒更新说明', waitHtml.includes('每2秒更新'));

// 检验点页：在线/掉线 + 掉线期间更新事件
const ptsHtml = await pages.points.render();
check('检验点页含掉线标注', ptsHtml.includes('掉线'));
check('检验点页含掉线期间更新事件', ptsHtml.includes('掉线期间更新'));
check('检验点页含模拟掉线按钮', ptsHtml.includes('模拟掉线') || ptsHtml.includes('恢复连接'));
check('检验点页含版本号', ptsHtml.includes('v3'));

// 原有页面回归：结果页与总览页可渲染
const resultsHtml = await pages.results.render();
check('结果档案页可渲染', resultsHtml.includes('计算结果'));
const overviewHtml = await pages.overview.render();
check('总览页可渲染', overviewHtml.includes('研究进度'));

process.exit(failures ? 1 : 0);
