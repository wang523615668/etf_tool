async function getJSON(url) {
  const response = await fetch(url);
  if (!response.ok) throw new Error(url);
  return response.json();
}

const actionText = { buy: '买入', sell: '卖出', hold: '持有', watch: '观察', reduce: '减仓', pause: '暂停' };
const planText = { long_win_150: '150份', long_win_s: 'S定投' };
const valuationActionText = { buy: '低估买入', watch: '观察', hold: '谨慎持有', reduce: '高估减仓', pause: '数据过期暂停' };
function htmlEscape(value) {
  return String(value ?? '').replace(/[&<>"]/g, char => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[char]));
}

function showReasonModal(action) {
  const modal = document.getElementById('reasonModal');
  const body = document.getElementById('reasonModalBody');
  if (!modal || !body) return;
  const detail = action.reason_detail;
  const matchNote = action.reason_match === 'same_day'
    ? '匹配方式：发车日同日社区发言（content/items 与 postId 非同一套 ID）'
    : (action.reason_match === 'url' ? '匹配方式：原文 URL 精确匹配' : '匹配方式：暂无');
  body.innerHTML = `
    <h2>${htmlEscape(action.date)} · ${htmlEscape(action.name)}</h2>
    <p class="muted">${htmlEscape(action.code)} · ${htmlEscape(planText[action.plan] || action.plan)} · ${htmlEscape(action.shares)} 份</p>
    <p class="muted">${htmlEscape(matchNote)}</p>
    <h3>发车原因</h3>
    <p>${htmlEscape(detail?.operation_reason || '暂未归档到当天文章原因：发车 content/items 与社区发言 postId 是两套体系，且该日暂无可用发言。')}</p>
    <h3>证据摘录</h3>
    <ul>${(detail?.reason_evidence || []).map(item => `<li>${htmlEscape(item)}</li>`).join('') || '<li>暂无</li>'}</ul>
    <p class="muted">估值：${htmlEscape(detail?.valuation_context || '-')} · 仓位：${htmlEscape(detail?.position_context || '-')}</p>
    ${detail?.risk_note ? `<p class="risk-note">风险提示：${htmlEscape(detail.risk_note)}</p>` : ''}
    ${detail?.url || action.url ? `<p><a href="${htmlEscape(detail?.url || action.url)}" target="_blank" rel="noopener noreferrer">打开原文/同日发言</a></p>` : ''}`;
  modal.classList.remove('hidden');
}

function closeReasonModal() {
  document.getElementById('reasonModal')?.classList.add('hidden');
}

function renderCards(data) {
  const stats150 = data.stats.long_win_150;
  const statsS = data.stats.long_win_s;
  const latestDate = data.recent_actions[0]?.date || '-';
  const valuation = data.valuation_freshness || {};
  const sourceLabel = data.data_source === 'lixinger' ? '理杏仁' : (data.data_source || 'local');
  document.getElementById('syncTime').textContent = `${data.generated_at?.replace('T', ' ') || '-'} · ${sourceLabel}`;
  document.getElementById('summaryCards').innerHTML = `
    <div class="card"><strong>${stats150.current}</strong><div class="label">150 当前份数 / 买${stats150.buy} 卖${stats150.sell}</div></div>
    <div class="card"><strong>${statsS.current}</strong><div class="label">S 当前份数 / 买${statsS.buy} 卖${statsS.sell}</div></div>
    <div class="card"><strong>${latestDate}</strong><div class="label">E大最近发车日期</div></div>
    <div class="card ${valuation.warning ? 'stale' : ''}"><strong>${valuation.snapshot_date || '-'}</strong><div class="label">估值快照日期 / ${valuation.index_count || 0} 个指数</div></div>
    <div class="card ${valuation.warning ? 'stale' : ''}"><strong>${valuation.cn_max_date || '-'}</strong><div class="label">A股理杏仁最新底层日期</div></div>
    <div class="card"><strong>${valuation.hk_max_date || '-'}</strong><div class="label">港股理杏仁最新底层日期</div></div>`;
  const warningBox = document.getElementById('freshnessWarning');
  if (warningBox) {
    const warnings = [data.data_warning, valuation.warning].filter(Boolean);
    warningBox.textContent = warnings.join('；') || '理杏仁底层数据新鲜度正常。';
    warningBox.className = warnings.length ? 'freshness-warning show' : 'freshness-warning';
  }
}

async function renderSunburst(plan = 'long_win_150') {
  const data = await getJSON('/api/sunburst/' + plan);
  const el = document.getElementById('sunburstChart');
  if (!el) return;
  if (typeof echarts === 'undefined') {
    el.innerHTML = '<div class="muted">图表库未加载（echarts），其它模块仍可用</div>';
    return;
  }
  const chart = echarts.init(el);
  chart.setOption({
    backgroundColor: 'transparent',
    tooltip: { formatter: p => `${p.name}<br/>份数：${p.value || ''}` },
    series: [{
      type: 'sunburst',
      data: data.children,
      radius: [0, '95%'],
      sort: null,
      label: { color: '#e8ecff', fontSize: 12 },
      levels: [
        {},
        { r0: '15%', r: '45%', itemStyle: { borderWidth: 2 }, label: { rotate: 'tangential' } },
        { r0: '45%', r: '82%', label: { align: 'right' } },
        { r0: '82%', r: '95%', label: { position: 'outside', padding: 3, silent: false } },
      ],
    }],
  });
  window.addEventListener('resize', () => chart.resize());
}

function renderSignals(signals, suppressed = [], decisionMemory = null) {
  const list = document.getElementById('signalList');
  const suppressedBox = document.getElementById('suppressedSignals');
  const meta = document.getElementById('signalMeta');
  if (!list) return;
  const active = signals || [];
  const cool = suppressed || [];
  if (meta) {
    const rule = decisionMemory?.rule || '买入后冷却；大跌可提前恢复';
    meta.textContent = `有效 ${active.length} · 冷却中 ${cool.length}`;
    meta.title = rule;
  }
  if (!active.length) {
    list.innerHTML = '<div class="empty-hint">今日无新的有效买卖提醒（可能都在冷却中）</div>';
  } else {
    list.innerHTML = active.map((signal, index) => `
      <div class="signal ${signal.action}" data-sig="${index}">
        <div class="action">${signal.date} · ${actionText[signal.action] || signal.action} · 置信度 ${signal.confidence ?? '-'}</div>
        <div class="name">${htmlEscape(signal.name)} <span class="muted">${htmlEscape(signal.code || '')}</span></div>
        <div class="reason">${htmlEscape(signal.reason || '')}</div>
        <div class="signal-actions">
          ${signal.action === 'buy' ? `<button class="btn-mini btn-buy-done" data-idx="${index}">我已买入</button>` : ''}
          ${signal.action === 'reduce' || signal.action === 'sell' ? `<button class="btn-mini btn-sell-done" data-idx="${index}">我已卖出/减仓</button>` : ''}
        </div>
      </div>`).join('');
    list.querySelectorAll('.btn-buy-done').forEach(btn => {
      btn.addEventListener('click', async () => {
        const s = active[Number(btn.dataset.idx)];
        await recordMyTrade({ ...s, action: 'buy' }, btn);
      });
    });
    list.querySelectorAll('.btn-sell-done').forEach(btn => {
      btn.addEventListener('click', async () => {
        const s = active[Number(btn.dataset.idx)];
        await recordMyTrade({ ...s, action: s.action === 'sell' ? 'sell' : 'reduce' }, btn);
      });
    });
  }
  if (suppressedBox) {
    if (!cool.length) {
      suppressedBox.innerHTML = '';
    } else {
      suppressedBox.innerHTML = `<div class="cooldown-title">冷却中（已执行，暂不重复提醒）</div>` + cool.map(signal => `
        <div class="signal cooldown ${signal.action}">
          <div class="action">${signal.date} · ${actionText[signal.action] || signal.action} · 冷却</div>
          <div class="name">${htmlEscape(signal.name)} <span class="muted">${htmlEscape(signal.code || '')}</span></div>
          <div class="reason">${htmlEscape(signal.cooldown?.reason || signal.reason || '')}</div>
        </div>`).join('');
    }
  }
}

async function recordMyTrade(signal, btn) {
  if (btn) {
    btn.disabled = true;
    btn.textContent = '记录中…';
  }
  try {
    const body = {
      action: signal.action || 'buy',
      name: signal.name,
      code: signal.code,
      category: signal.category,
      shares: signal.shares ?? 1,
      price: signal.cp ?? signal.price ?? null,
      source_signal_date: signal.date,
      note: 'from dashboard signal',
    };
    const res = await fetch('/api/my-trades', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    const data = await res.json();
    if (!data.ok) throw new Error(data.error || 'record failed');
    if (btn) btn.textContent = '已记录';
    // refresh summary so signal moves into cooldown immediately
    const summary = await getJSON('/api/summary');
    renderSignals(summary.signals || [], summary.suppressed_signals || [], summary.decision_memory || null);
    renderMyTrades();
  } catch (err) {
    if (btn) {
      btn.disabled = false;
      btn.textContent = '重试';
    }
    alert('记录失败：' + (err.message || err));
  }
}

async function renderMyTrades() {
  const box = document.getElementById('myTradeList');
  const meta = document.getElementById('myTradeMeta');
  if (!box) return;
  try {
    const data = await getJSON('/api/my-trades');
    const trades = data.trades || [];
    const st = data.settings || {};
    if (meta) {
      meta.textContent = `${trades.length} 条 · 买冷却${st.buy_cooldown_days ?? 3}天/跌${Math.round((st.buy_drop_resume_pct || 0.03) * 100)}%恢复`;
    }
    if (!trades.length) {
      box.innerHTML = '<div class="muted">还没有个人成交记录。收到买入提醒并实际买入后，点「我已买入」。</div>';
      return;
    }
    box.innerHTML = trades.slice(0, 12).map(t => `
      <article class="talk-item">
        <div class="topic-source">${htmlEscape(t.date)} · ${actionText[t.action] || t.action} · ${htmlEscape(t.category || '')}</div>
        <h3>${htmlEscape(t.name || '-')} <span class="muted">${htmlEscape(t.code || '')}</span></h3>
        <p>${t.shares != null ? htmlEscape(t.shares) + ' 份' : ''} ${t.price != null ? ' · 点位/价 ' + htmlEscape(t.price) : ''} ${t.note ? ' · ' + htmlEscape(t.note) : ''}</p>
        <button class="btn-mini btn-del-trade" data-id="${htmlEscape(t.id)}">删除</button>
      </article>`).join('');
    box.querySelectorAll('.btn-del-trade').forEach(btn => {
      btn.addEventListener('click', async () => {
        await fetch('/api/my-trades/' + encodeURIComponent(btn.dataset.id), { method: 'DELETE' });
        const summary = await getJSON('/api/summary');
        renderSignals(summary.signals || [], summary.suppressed_signals || [], summary.decision_memory || null);
        renderMyTrades();
      });
    });
  } catch (err) {
    if (meta) meta.textContent = '加载失败';
    box.innerHTML = `<div class="muted">${htmlEscape(err.message || err)}</div>`;
  }
}

function renderRecentActions(actions) {
  const target = document.getElementById('recentActions');
  target.innerHTML = actions.map((action, index) => `
    <button class="action-card ${action.action} clickable" data-index="${index}">
      <div class="action-date">${action.date} · ${planText[action.plan] || action.plan}</div>
      <div class="action-title"><span class="tag ${action.action}">${actionText[action.action] || action.action}</span>${action.name}</div>
      <div class="muted">${action.code} · ${action.shares} 份 · ${action.category}</div>
      <div class="reason-status ${action.reason_quality?.level || 'missing'}">${action.reason_quality?.label || (action.reason_detail ? '已归档原因' : '原因待归档')}，点击查看</div>
    </button>`).join('');
  target.querySelectorAll('button').forEach(button => {
    button.addEventListener('click', () => showReasonModal(actions[Number(button.dataset.index)]));
  });
}

function pct(value) {
  return value === null || value === undefined ? '-' : `${Math.round(value * 100)}%`;
}

function num(value) {
  return value === null || value === undefined ? '-' : Number(value).toFixed(2);
}

function detailHref(name) {
  return `/index-detail#${encodeURIComponent(name || '')}`;
}

function renderValuations(dashboard) {
  const target = document.getElementById('valuationList');
  const board = document.getElementById('decisionBoard');
  const meta = document.getElementById('valuationMeta');
  if (!target) return;
  const rows = Array.isArray(dashboard) ? dashboard : (dashboard.rows || []);
  const counts = dashboard.counts || {};
  if (meta) meta.textContent = `${dashboard.total || rows.length} 个指数 · 低估 ${counts.buy || 0} · 高估 ${counts.reduce || 0} · ${counts.pause ? `过期 ${counts.pause}` : '数据正常'}`;
  if (board) {
    const top = dashboard.top || {};
    const blocks = [
      ['buy', '低估候选', '优先复核仓位，分批而不是一次性买入'],
      ['watch', '观察等待', '赔率一般，等待更低温度或 E大信号'],
      ['reduce', '高估风险', '只做减仓/暂停加仓复盘，不追高'],
    ];
    if (counts.pause) blocks.push(['pause', '数据过期', '底层日期未更新，暂停自动判断']);
    board.innerHTML = blocks.map(([key, title, hint]) => `
      <div class="decision-card ${key}">
        <div class="decision-title"><b>${title}</b><span>${(top[key] || []).length} 条</span></div>
        <p>${hint}</p>
        <ul>${(top[key] || []).slice(0, 5).map(row => `<li><a href="${detailHref(row.name)}">${htmlEscape(row.name)}</a><b>${row.temperature ?? '-'}°</b></li>`).join('') || '<li><span>暂无</span><b>-</b></li>'}</ul>
      </div>`).join('');
  }
  target.innerHTML = rows.slice(0, 16).map(row => `
    <a class="valuation-card ${row.action}" href="${detailHref(row.name)}" style="text-decoration:none;color:inherit;display:block">
      <div class="valuation-head"><b>${htmlEscape(row.name)}</b><span class="tag ${row.action}">${valuationActionText[row.action] || row.action}</span></div>
      <div class="temperature"><strong>${row.temperature ?? '-'}</strong><span>估值温度</span></div>
      <div class="valuation-metrics">
        <span>PE ${num(row.pe)}</span><span>PE分位 ${pct(row.pe_percentile)}</span>
        <span>PB ${num(row.pb)}</span><span>PB分位 ${pct(row.pb_percentile)}</span>
      </div>
      <div class="reason">${htmlEscape(row.reason || '')}</div>
      <div class="muted" style="margin-top:8px">点击查看历史分位曲线 →</div>
    </a>`).join('');
}

function renderExposure(exposure) {
  const target = document.getElementById('exposureGrid');
  if (!target) return;
  target.innerHTML = Object.entries(exposure || {}).map(([key, plan]) => `
    <div class="exposure-card">
      <div class="valuation-head"><b>${plan.name}</b><span>${plan.positions_count} 只 / ${plan.category_count} 类</span></div>
      <div class="muted">总份数 ${Math.round(plan.total_shares || 0)}</div>
      ${(plan.top_categories || []).map(item => `
        <div class="exposure-row">
          <div><span>${item.name}</span><b>${Math.round((item.weight || 0) * 100)}%</b></div>
          <i style="width:${Math.max(2, Math.round((item.weight || 0) * 100))}%"></i>
        </div>`).join('')}
    </div>`).join('');
}

function renderTopics(data, selectedIndex = 0) {
  const tabs = document.getElementById('topicTabs');
  const items = document.getElementById('topicItems');
  const meta = document.getElementById('topicMeta');
  if (!tabs || !items || !meta) return;
  const topics = data.topics || [];
  meta.textContent = `${data.topic_count || topics.length} 个主题 · ${data.total_items || 0} 条内容 · ${String(data.generated_at || '').replace('T', ' ')}`;
  if (!topics.length) {
    tabs.innerHTML = '';
    items.innerHTML = '<div class="muted">主题知识库重建中，请稍后刷新。</div>';
    return;
  }
  tabs.innerHTML = topics.map((topic, index) => `
    <button class="topic-tab ${index === selectedIndex ? 'active' : ''}" data-index="${index}">${htmlEscape(topic.name)}<span>${topic.count}</span></button>
  `).join('');
  const topic = topics[selectedIndex] || topics[0];
  items.innerHTML = topic.items.map(item => `
    <article class="topic-item">
      <div class="topic-source">${htmlEscape(item.source || '-')} · ${htmlEscape(item.date || '无日期')} · ${htmlEscape(item.id || '-')}</div>
      <h3><a href="${htmlEscape(item.url || '#')}" target="_blank" rel="noopener noreferrer">${htmlEscape(item.title || '未命名')}</a></h3>
      <p>${htmlEscape(item.excerpt || '')}</p>
      <div class="keyword-row">${(item.keywords || []).map(keyword => `<span>${htmlEscape(keyword)}</span>`).join('')}</div>
    </article>
  `).join('') || '<div class="muted">该主题暂无条目</div>';
  tabs.querySelectorAll('button').forEach(button => {
    button.addEventListener('click', () => renderTopics(data, Number(button.dataset.index)));
  });
}

async function renderCompare() {
  const rows = await getJSON('/api/compare');
  document.getElementById('compareTable').innerHTML = rows.map(row => `
    <tr>
      <td>${row.date}</td><td>${row.source}</td>
      <td><span class="tag ${row.action}">${actionText[row.action] || row.action}</span></td>
      <td>${row.name}<br/><span class="muted">${row.code}</span></td>
      <td>${row.shares}</td><td>${row.gap}</td>
    </tr>`).join('');
}

function renderCalibration(cal) {
  const meta = document.getElementById('calibrationMeta');
  const cards = document.getElementById('calibrationCards');
  const samples = document.getElementById('calibrationSamples');
  if (!meta || !cards || !samples || !cal) return;
  const rate = Math.round((cal.match_rate || 0) * 100);
  meta.textContent = `匹配率 ${rate}% · 窗口 ${cal.window_days || 45} 天 · 共 ${cal.total || 0} 条`;
  cards.innerHTML = [
    ['matched', '一致', cal.matched || 0, '本地与E大同向'],
    ['watch', '本地独有', cal.local_unmatched || 0, '本地提醒了但E大未同向'],
    ['reduce', 'E大独有', cal.ed_unmatched || 0, 'E大操作了但本地未提醒'],
  ].map(([cls, title, n, hint]) => `
    <div class="decision-card ${cls}">
      <div class="decision-title"><b>${title}</b><span>${n}</span></div>
      <p>${hint}</p>
    </div>`).join('');
  const packs = [
    ['一致样本', cal.samples?.matched || []],
    ['本地未对齐', cal.samples?.local_unmatched || []],
    ['E大未提醒', cal.samples?.ed_unmatched || []],
  ];
  samples.innerHTML = packs.map(([title, rows]) => `
    <article class="topic-item">
      <div class="topic-source">${title} · ${rows.length} 条</div>
      <ul class="calibration-list">${rows.slice(0, 5).map(r =>
        `<li><b>${htmlEscape(r.date || '')}</b> ${htmlEscape(actionText[r.action] || r.action || '')} ${htmlEscape(r.name || '')}<span class="muted"> ${htmlEscape(r.gap || '')}</span></li>`
      ).join('') || '<li class="muted">暂无</li>'}</ul>
    </article>`).join('');
}

function renderEdTalks(data) {
  const meta = document.getElementById('edTalkMeta');
  const list = document.getElementById('edTalkList');
  if (!meta || !list) return;
  const items = data.items || [];
  meta.textContent = `${data.total || items.length} 篇 · ${String(data.generated_at || '').replace('T', ' ') || '未抓取'}`;
  list.innerHTML = items.slice(0, 12).map(item => `
    <article class="talk-item">
      <div class="topic-source">${htmlEscape(item.date || '-')} · ${htmlEscape(item.action?.name || item.title || '')}</div>
      <h3><a href="${htmlEscape(item.url || '#')}" target="_blank" rel="noopener noreferrer">${htmlEscape(item.title || item.url || '发言记录')}</a></h3>
      <p>${htmlEscape(item.operation_reason || item.text_excerpt || '')}</p>
      <div class="keyword-row"><span>${htmlEscape(item.valuation_context || '估值未明确')}</span><span>${htmlEscape(item.position_context || '仓位未明确')}</span></div>
    </article>`).join('') || '<div class="muted">暂无归档，请等待定时任务抓取。</div>';
}

async function init() {
  // Progressive load: paint overview first, then heavy modules.
  // Avoid waiting on ed-talks/topics before replacing 读取中…
  document.getElementById('syncTime').textContent = '加载中…';

  document.getElementById('planSelect')?.addEventListener('change', event => renderSunburst(event.target.value));
  document.getElementById('reasonModalClose')?.addEventListener('click', closeReasonModal);
  document.getElementById('reasonModal')?.addEventListener('click', (event) => {
    if (event.target?.id === 'reasonModal') closeReasonModal();
  });

  let summary = null;
  try {
    summary = await getJSON('/api/summary');
    renderCards(summary);
    renderRecentActions(summary.recent_actions || []);
    renderSignals(summary.signals || [], summary.suppressed_signals || [], summary.decision_memory || null);
    renderValuations(summary.valuation_dashboard || summary.valuation_rows || []);
    renderExposure(summary.plan_exposure || {});
    if (summary.calibration) renderCalibration(summary.calibration);
    renderMyTrades();
  } catch (err) {
    const sync = document.getElementById('syncTime');
    if (sync) sync.textContent = '摘要接口失败';
    const warn = document.getElementById('freshnessWarning');
    if (warn) {
      warn.className = 'freshness-warning show';
      warn.textContent = '首页摘要加载失败：' + (err && err.message || err);
    }
  }

  // secondary modules — independent, non-blocking for first paint
  const secondary = [
    getJSON('/api/valuations').then(v => {
      if (!summary) renderValuations(v);
    }).catch(() => {}),
    getJSON('/api/calibration').then(c => {
      if (!(summary && summary.calibration)) renderCalibration(c);
    }).catch(() => {}),
    getJSON('/api/topics').then(renderTopics).catch(err => {
      const meta = document.getElementById('topicMeta');
      if (meta) meta.textContent = '主题库加载失败';
      console.warn('topics', err);
    }),
    getJSON('/api/ed-talks?limit=20').then(renderEdTalks).catch(err => {
      const meta = document.getElementById('edTalkMeta');
      if (meta) meta.textContent = '发言归档加载失败';
      console.warn('ed-talks', err);
    }),
    renderSunburst().catch(e => console.warn('sunburst', e)),
    renderCompare().catch(e => console.warn('compare', e)),
  ];
  await Promise.allSettled(secondary);
}

init().catch(error => {
  const sync = document.getElementById('syncTime');
  if (sync) sync.textContent = '加载失败';
  document.body.insertAdjacentHTML('beforeend', `<pre style="color:#ff6b6b;padding:12px">${error && error.stack || error}</pre>`);
});
