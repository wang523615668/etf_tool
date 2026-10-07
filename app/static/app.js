// summary 请求去重：页面初始化/记录成交/删除成交可能并发请求 /api/summary，
// 共享同一个 in-flight Promise，避免重复拉 150KB 大响应。
let _summaryInflight = null;
async function getJSON(url) {
  if (url === '/api/summary' && _summaryInflight) return _summaryInflight;
  const p = (async () => {
    const response = await fetch(url);
    if (!response.ok) throw new Error(url);
    return response.json();
  })();
  if (url === '/api/summary') {
    _summaryInflight = p;
    p.finally(() => { if (_summaryInflight === p) _summaryInflight = null; });
  }
  return p;
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
  const sourceLabel = data.data_source === 'lixinger' ? '理杏仁' : (data.data_source === 'self' ? '自算引擎' : (data.data_source || 'local'));
  const sm = data.ledger?.summary || {};
  const mp = data.market_position || data.ledger?.market_position || {};
  const tgt = mp.target_position_pct != null ? (mp.target_position_pct * 100).toFixed(1) + '%' : '-';
  const cur = mp.current_position_pct != null ? (mp.current_position_pct * 100).toFixed(1) + '%' : '-';
  document.getElementById('syncTime').textContent = `${data.generated_at?.replace('T', ' ') || '-'} · ${sourceLabel}`;
  document.getElementById('summaryCards').innerHTML = `
    <div class="card"><strong>${sm.equity_rmb != null ? Number(sm.equity_rmb).toLocaleString('zh-CN') : '-'}</strong><div class="label">我的权益（元）</div></div>
    <div class="card ${Number(sm.equity_pnl_rmb||0) >= 0 ? '' : 'stale'}"><strong>${sm.equity_pnl_rmb != null ? Number(sm.equity_pnl_rmb).toLocaleString('zh-CN') : '-'}</strong><div class="label">总盈亏 / ${sm.equity_return_pct != null ? (sm.equity_return_pct*100).toFixed(2)+'%' : '-'}</div></div>
    <div class="card"><strong>${sm.used_shares ?? '-'} / ${data.ledger?.account?.total_shares ?? 150}</strong><div class="label">已用份数 / 总额度</div></div>
    <div class="card ${mp.over_target ? 'stale' : ''}"><strong>${cur} → ${tgt}</strong><div class="label">当前总仓 → A股全指目标</div></div>
    <div class="card"><strong>${stats150.current}</strong><div class="label">E大150 当前份数 / 买${stats150.buy} 卖${stats150.sell}</div></div>
    <div class="card ${valuation.warning ? 'stale' : ''}"><strong>${valuation.snapshot_date || '-'}</strong><div class="label">估值快照日期 / ${valuation.index_count || 0} 个指数</div></div>
    <div class="card"><strong>${latestDate}</strong><div class="label">E大最近发车日期</div></div>`;
  const warningBox = document.getElementById('freshnessWarning');
  if (warningBox) {
    const warnings = [data.data_warning, valuation.warning].filter(Boolean);
    warningBox.textContent = warnings.join('；') || '理杏仁底层数据新鲜度正常。';
    warningBox.className = warnings.length ? 'freshness-warning show' : 'freshness-warning';
  }
}

function money(v) {
  if (v === null || v === undefined || Number.isNaN(Number(v))) return '—';
  return Number(v).toLocaleString('zh-CN', { maximumFractionDigits: 0 });
}

function pct2(v) {
  if (v === null || v === undefined || Number.isNaN(Number(v))) return '—';
  return (Number(v) * 100).toFixed(2) + '%';
}

function renderLedger(ledger) {
  const cards = document.getElementById('ledgerCards');
  const table = document.getElementById('ledgerTable');
  const groups = document.getElementById('ledgerGroups');
  const meta = document.getElementById('ledgerMeta');
  if (!cards || !table) return;
  if (!ledger) {
    cards.innerHTML = '<div class="muted">账本加载中…</div>';
    return;
  }
  const sm = ledger.summary || {};
  const acc = ledger.account || {};
  if (meta) {
    meta.textContent = `本金${money(acc.principal)} · 每份${money(acc.unit_value)} · 同类≤${Math.round((acc.category_cap_pct||0.25)*100)}% · 总仓跟A股全指`;
  }
  cards.innerHTML = `
    <div class="decision-card"><div class="label">权益</div><strong>${money(sm.equity_rmb)}</strong></div>
    <div class="decision-card"><div class="label">现金</div><strong>${money(sm.cash_rmb)}</strong></div>
    <div class="decision-card"><div class="label">持仓市值</div><strong>${money(sm.market_value_rmb)}</strong></div>
    <div class="decision-card"><div class="label">持仓盈亏</div><strong style="color:${Number(sm.position_pnl_rmb||0)>=0?'#18c37e':'#ff6b6b'}">${money(sm.position_pnl_rmb)} (${pct2(sm.position_return_pct)})</strong></div>
    <div class="decision-card"><div class="label">总收益率</div><strong style="color:${Number(sm.equity_pnl_rmb||0)>=0?'#18c37e':'#ff6b6b'}">${pct2(sm.equity_return_pct)}</strong></div>
    <div class="decision-card"><div class="label">已用份数</div><strong>${sm.used_shares ?? 0} / ${acc.total_shares ?? 150}</strong></div>`;
  const positions = ledger.positions || [];
  if (!positions.length) {
    table.innerHTML = '<tr><td colspan="7" class="muted">暂无持仓。在「今日买卖提醒」点「我已买入」后会自动记账。</td></tr>';
  } else {
    table.innerHTML = positions.map(p => `
      <tr>
        <td>${htmlEscape(p.name)} <span class="muted">${htmlEscape(p.code||'')}</span></td>
        <td>${htmlEscape(p.group || p.category || '')}</td>
        <td>${htmlEscape(p.shares)}</td>
        <td>${money(p.cost_rmb)}</td>
        <td>${money(p.market_rmb)}</td>
        <td style="color:${Number(p.pnl_rmb||0)>=0?'#18c37e':'#ff6b6b'}">${money(p.pnl_rmb)}</td>
        <td style="color:${Number(p.return_pct||0)>=0?'#18c37e':'#ff6b6b'}">${pct2(p.return_pct)}</td>
      </tr>`).join('');
  }
  if (groups) {
    const gs = ledger.groups || [];
    groups.innerHTML = gs.map(g => `
      <div class="exposure-card ${g.at_cap ? 'stale' : ''}">
        <div class="name">${htmlEscape(g.group)}</div>
        <div class="value">${money(g.market_rmb)} · ${pct2(g.weight_pct)}</div>
        <div class="muted">上限 ${pct2(g.cap_pct)} · 余量 ${money(g.headroom_rmb)}${g.at_cap ? ' · 已满' : ''}</div>
      </div>`).join('') || '<div class="muted">暂无分类敞口</div>';
  }
}

function renderMarketPosition(mp) {
  const cards = document.getElementById('marketPositionCards');
  const meta = document.getElementById('marketPositionMeta');
  if (!cards) return;
  if (!mp || mp.target_position_pct == null) {
    cards.innerHTML = `<div class="muted">A股全指仓位指引加载失败${mp?.error ? '：' + htmlEscape(mp.error) : '…'}</div>`;
    if (meta) meta.textContent = '无数据';
    return;
  }
  const avg = mp.avg_percentile != null ? (mp.avg_percentile * 100).toFixed(1) + '%' : '—';
  const peP = mp.pe_percentile != null ? (mp.pe_percentile * 100).toFixed(1) + '%' : '—';
  const pbP = mp.pb_percentile != null ? (mp.pb_percentile * 100).toFixed(1) + '%' : '—';
  const tgt = (mp.target_position_pct * 100).toFixed(1) + '%';
  const cur = mp.current_position_pct != null ? (mp.current_position_pct * 100).toFixed(1) + '%' : '—';
  const headSh = mp.headroom_shares != null ? Number(mp.headroom_shares).toFixed(1) : '—';
  const headRmb = money(mp.headroom_rmb);
  if (meta) {
    meta.textContent = `${mp.index || 'A股全指'} · ${mp.window_years || 10}年 · 数据 ${mp.snapshot_date || '—'}`;
  }
  cards.innerHTML = `
    <div class="decision-card"><div class="label">10年综合分位</div><strong>${avg}</strong><div class="muted">PE ${peP} / PB ${pbP}</div></div>
    <div class="decision-card"><div class="label">目标总仓位</div><strong style="color:#2563eb">${tgt}</strong><div class="muted">= 1 − 分位</div></div>
    <div class="decision-card ${mp.over_target ? 'stale' : ''}"><div class="label">当前总仓</div><strong>${cur}</strong><div class="muted">持仓市值/本金</div></div>
    <div class="decision-card"><div class="label">总仓余量</div><strong>${headSh} 份</strong><div class="muted">约 ${headRmb} 元</div></div>
    <div class="decision-card"><div class="label">目标金额/份</div><strong>${money(mp.target_rmb)}</strong><div class="muted">${mp.target_shares != null ? Number(mp.target_shares).toFixed(1) : '—'} / 150 份</div></div>
    <div class="decision-card"><div class="label">今日最多可新买</div><strong>${mp.max_new_shares_by_market ?? 0} 份</strong><div class="muted">${mp.over_target ? '已超目标·禁新开' : '受总仓约束'}</div></div>`;
}

function renderActionSheet(sheet) {
  const list = document.getElementById('actionSheetList');
  const blockedBox = document.getElementById('actionSheetBlocked');
  const meta = document.getElementById('actionSheetMeta');
  if (!list) return;
  if (!sheet) {
    list.innerHTML = '<div class="muted">动作单加载中…</div>';
    return;
  }
  const actions = sheet.actions || [];
  const blocked = sheet.blocked || [];
  const cool = sheet.cooldown || [];
  if (meta) meta.textContent = `可执行 ${actions.filter(a=>a.status==='actionable').length} · 拦截 ${blocked.length} · 冷却 ${cool.length}` +
    (sheet.market_position?.target_position_pct != null
      ? ` · 目标总仓${(sheet.market_position.target_position_pct*100).toFixed(1)}%`
      : '');
  const actionable = actions.filter(a => a.status === 'actionable' || a.status === 'watch');
  const pending = sheet.pending_reminders || [];
  if (!actionable.length && !pending.length) {
    list.innerHTML = '<div class="empty-hint">今日无新增可执行动作（可能都在冷却/已满仓）</div>';
  } else {
    list.innerHTML = (pending.length ? `<div class="cooldown-title">持续提醒（已操作自动停 / 未操作一直催）</div>` : '') + actionable.map((a, index) => `
      <div class="signal ${a.action || 'watch'}">
        <div class="action">${htmlEscape(a.date || '')} · ${actionText[a.action] || a.action || '观察'} · 建议 ${a.suggested_shares ?? 0} 份</div>
        <div class="name">${htmlEscape(a.name || '')} <span class="muted">${htmlEscape(a.group || a.category || '')}</span></div>
        <div class="reason">${htmlEscape(a.checklist || a.reason || '')}</div>
        ${(a.status === 'actionable') ? `<div class="signal-actions">
          <button class="btn-mini btn-buy-done" data-as-idx="${index}">✅ 已操作</button>
          <button class="btn-mini btn-dismiss" data-as-idx="${index}" style="opacity:.75">⏸ 暂不操作(忽略30天)</button>
        </div>` : ''}
      </div>`).join('')
      + pending.map(r => {
          const n = Number(r.count || 1);
          const since = r.first_date ? `自${r.first_date}` : '';
          return `<div class="signal cooldown">
            <div class="action">仍未处理 · ${r.action === 'buy' ? '买入' : '卖出/减仓'} · 已提醒${n > 1 ? ` ${n} 次` : ''}${since}</div>
            <div class="name">${htmlEscape(r.name || '')}</div>
            <div class="signal-actions">
              <button class="btn-mini btn-pending-done" data-pr-key="${htmlEscape(r.key)}" data-pr-name="${htmlEscape(r.name || '')}" data-pr-action="${r.action}">✅ 已操作，停止提醒</button>
              <button class="btn-mini btn-pending-dismiss" data-pr-key="${htmlEscape(r.key)}" data-pr-name="${htmlEscape(r.name || '')}" style="opacity:.75">⏸ 不操作(忽略30天)</button>
            </div>
          </div>`;
        }).join('');
    // 今日信号：已操作 → 记成交（走原逻辑，含冷却压制）
    list.querySelectorAll('.btn-buy-done').forEach(btn => {
      btn.addEventListener('click', async () => {
        const a = actionable[Number(btn.dataset.asIdx)];
        await recordMyTrade({ ...a, action: a.action || 'buy', shares: a.suggested_shares || 1 }, btn);
      });
    });
    // 今日信号：不操作 → 忽略30天
    list.querySelectorAll('.btn-dismiss').forEach(btn => {
      btn.addEventListener('click', async () => {
        const a = actionable[Number(btn.dataset.asIdx)];
        await dismissSignal({ name: a.name, code: a.code }, btn, '已忽略，30天内不再提醒');
      });
    });
    // 历史未决：已操作
    list.querySelectorAll('.btn-pending-done').forEach(btn => {
      btn.addEventListener('click', async () => {
        await confirmPendingBought(btn.dataset.prName, btn.dataset.prAction, btn);
      });
    });
    // 历史未决：不操作
    list.querySelectorAll('.btn-pending-dismiss').forEach(btn => {
      btn.addEventListener('click', async () => {
        await dismissSignal({ name: btn.dataset.prName }, btn, '已忽略，30天内不再提醒');
      });
    });
  }
  if (blockedBox) {
    const parts = [];
    if (blocked.length) {
      parts.push(`<div class="cooldown-title">风控拦截（同类25%/现金/份额）</div>` + blocked.map(a => `
        <div class="signal cooldown">
          <div class="action">${htmlEscape(a.name)} · 拦截</div>
          <div class="reason">${htmlEscape(a.block_reason || '')}</div>
        </div>`).join(''));
    }
    if (cool.length) {
      parts.push(`<div class="cooldown-title">冷却中</div>` + cool.map(a => `
        <div class="signal cooldown">
          <div class="action">${htmlEscape(a.name)} · 冷却</div>
          <div class="reason">${htmlEscape(a.cooldown?.reason || a.reason || '')}</div>
        </div>`).join(''));
    }
    blockedBox.innerHTML = parts.join('');
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
      label: { color: '#33415e', fontSize: 12 },
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

async function confirmPendingBought(name, action, btn) {
  if (btn) { btn.disabled = true; btn.textContent = '记录中…'; }
  try {
    const res = await fetch('/api/pending-reminders/bought', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name, action: action === 'buy' ? 'buy' : 'sell' }),
    });
    const data = await res.json();
    if (!data.ok) throw new Error(data.error || 'failed');
    if (btn) btn.textContent = '已记录';
    await refreshAfterAction();
  } catch (err) {
    if (btn) { btn.disabled = false; btn.textContent = '重试'; }
    alert('记录失败：' + (err.message || err));
  }
}

async function dismissSignal(sig, btn, okText) {
  if (btn) { btn.disabled = true; btn.textContent = '处理中…'; }
  try {
    const res = await fetch('/api/pending-reminders/dismiss', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: sig.name }),
    });
    const data = await res.json();
    if (!data.ok) throw new Error('未找到对应提醒');
    if (btn) btn.textContent = okText || '已忽略';
    await refreshAfterAction();
  } catch (err) {
    if (btn) { btn.disabled = false; btn.textContent = '重试'; }
    alert('操作失败：' + (err.message || err));
  }
}

async function refreshAfterAction() {
  const summary = await getJSON('/api/summary');
  renderCards(summary);
  renderSignals(summary.signals || [], summary.suppressed_signals || [], summary.decision_memory || null);
  renderLedger(summary.ledger);
  renderMarketPosition(summary.market_position || summary.ledger?.market_position);
  renderActionSheet(summary.action_sheet);
  renderMyTrades();
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
    renderCards(summary);
    renderSignals(summary.signals || [], summary.suppressed_signals || [], summary.decision_memory || null);
    renderLedger(summary.ledger);
    renderMarketPosition(summary.market_position || summary.ledger?.market_position);
    renderActionSheet(summary.action_sheet);
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
      meta.textContent = `${trades.length} 条 · 买冷却${st.buy_cooldown_days ?? 30}天/跌${Math.round((st.buy_drop_resume_pct || 0.10) * 100)}%恢复`;
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

function renderDecision(dash) {
  const target = document.getElementById('decisionList');
  const meta = document.getElementById('decisionMeta');
  if (!target) return;
  const rows = dash.rows || [];
  if (meta) meta.textContent = `${dash.total || rows.length} 个品种 · ${dash.generated_at || ''}`;
  const order = { '买入': 0, '可买入': 1, '持有': 2, '观望': 3, '减仓': 4, '卖出': 5, '暂停': 6 };
  const sorted = [...rows].sort((a, b) => (order[a.decision] ?? 9) - (order[b.decision] ?? 9) || (a.temperature ?? 999) - (b.temperature ?? 999));
  target.innerHTML = sorted.slice(0, 18).map(row => {
    const conf = row.confidence === '高' ? '🔥' : row.confidence === '中' ? '⚡' : '';
    const cls = (row.decision === '买入' || row.decision === '可买入') ? 'buy' : (row.decision === '卖出' || row.decision === '减仓') ? 'reduce' : (row.decision === '持有') ? 'hold' : '';
    return `
    <a class="valuation-card ${cls}" href="${detailHref(row.name)}" style="text-decoration:none;color:inherit;display:block">
      <div class="valuation-head"><b>${htmlEscape(row.name)}</b><span class="tag ${cls}">${row.decision} ${conf}</span></div>
      <div class="temperature"><strong>${row.temperature ?? '-'}</strong><span>估值温度</span></div>
      <div class="valuation-metrics">
        <span>E大信号 ${row.ed_signals ?? 0}</span><span>买${row.ed_buys ?? 0}/卖${row.ed_sells ?? 0}</span>
        <span>${row.my_bought ? '✅ 已持有' : '未持有'}</span><span>置信 ${row.confidence}</span>
      </div>
      <div class="reason">${htmlEscape(row.decision_basis || '')}</div>
      <div class="muted" style="margin-top:8px">${htmlEscape(row.ed_hint || '')}</div>
    </a>`;
  }).join('');
}

function renderValuations(dashboard) {
  const target = document.getElementById('valuationList');
  const board = document.getElementById('decisionBoard');
  const meta = document.getElementById('valuationMeta');
  if (!target) return;
  const rows = Array.isArray(dashboard) ? dashboard : (dashboard.rows || []);
  const counts = dashboard.counts || {};
  const isQieman = (dashboard.data_source === 'qieman') || (rows[0] && rows[0].source === 'qieman');
  if (meta) {
    const modeNote = isQieman ? ' · 且慢10年百分位' : ' · 本地分位';
    meta.textContent = `${dashboard.total || rows.length} 个指数${modeNote} · 低估 ${counts.buy || 0} · 高估 ${counts.reduce || 0} · ${counts.pause ? `过期 ${counts.pause}` : '数据正常'}`;
  }
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
  const fmtDev = (v) => {
    if (v == null || Number.isNaN(Number(v))) return '—';
    const n = Number(v);
    return (n > 0 ? '+' : '') + n.toFixed(1) + '%';
  };
  target.innerHTML = rows.slice(0, 16).map(row => {
    const peExt = (row.pe_hist_min_pct != null || row.pe_hist_max_pct != null)
      ? `${fmtDev(row.pe_hist_min_pct)}~${fmtDev(row.pe_hist_max_pct)}` : '—';
    const pbExt = (row.pb_hist_min_pct != null || row.pb_hist_max_pct != null)
      ? `${fmtDev(row.pb_hist_min_pct)}~${fmtDev(row.pb_hist_max_pct)}` : '—';
    const interest = row.mean_dev_interest_score != null
      ? `兴趣${row.mean_dev_interest_score}·${htmlEscape(row.mean_dev_interest_level || '')}`
      : '';
    // 且慢字段：value=PE或PB值, percentile=百分位(0-100), high_10y/low_10y/roe
    if (isQieman) {
      const metric = row.metric || 'PE';
      const val = row.value != null ? num(row.value) : '—';
      const pctStr = row.percentile != null ? row.percentile.toFixed(1) + '%' : '—';
      const high = row.high_10y != null ? num(row.high_10y) : '—';
      const low = row.low_10y != null ? num(row.low_10y) : '—';
      const roe = row.roe != null ? num(row.roe) : '—';
      return `
      <a class="valuation-card ${row.action}" href="${detailHref(row.name)}" style="text-decoration:none;color:inherit;display:block">
        <div class="valuation-head"><b>${htmlEscape(row.name)}</b><span class="tag ${row.action}">${valuationActionText[row.action] || row.action}</span></div>
        <div class="temperature"><strong>${row.temperature ?? '-'}</strong><span>估值温度</span></div>
        <div class="valuation-metrics">
          <span>${metric} ${val}</span><span>且慢百分位 ${pctStr}</span>
          <span>10年最高 ${high}</span><span>10年最低 ${low}</span>
          <span>ROE ${roe}</span><span>口径 10年</span>
        </div>
        <div class="reason">${htmlEscape(row.reason || '')}</div>
        <div class="muted" style="margin-top:8px">数据来源：且慢每日估值 →</div>
      </a>`;
    }
    return `
    <a class="valuation-card ${row.action}" href="${detailHref(row.name)}" style="text-decoration:none;color:inherit;display:block">
      <div class="valuation-head"><b>${htmlEscape(row.name)}</b><span class="tag ${row.action}">${valuationActionText[row.action] || row.action}</span></div>
      <div class="temperature"><strong>${row.temperature ?? '-'}</strong><span>估值温度</span></div>
      ${row.double_avg_buy ? '<div class="double-avg-badge">✅ 双均线低估</div>' : ''}
      ${row.self_engine ? `
      <div class="valuation-metrics">
        <span>PE ${num(row.pe)}</span><span>PB ${num(row.pb)}</span>
        <span>现/5年均 ${fmtDev(row.pe_vs5y)}</span><span>PE 5年分位 ${row.pe_pct5y != null ? row.pe_pct5y.toFixed(1) + '%' : '—'}</span>
        <span>现/10年均 ${fmtDev(row.pe_vs10y)}</span><span>PE 10年分位 ${row.pe_pct10y != null ? row.pe_pct10y.toFixed(1) + '%' : '—'}</span>
        <span>5年均PE ${num(row.pe_avg5y)}</span><span>10年均PE ${num(row.pe_avg10y)}</span>
        <span>历史高低 ${num(row.pe_hist_max)}/${num(row.pe_hist_min)}</span><span>PB现/5年 ${fmtDev(row.pb_vs5y)}</span>
      </div>` : `
      <div class="valuation-metrics">
        <span>PE ${num(row.pe)}</span><span>PE分位 ${pct(row.pe_percentile)}</span>
        <span>PB ${num(row.pb)}</span><span>PB分位 ${pct(row.pb_percentile)}</span>
        <span>PE偏离 ${fmtDev(row.pe_dev_pct)}</span><span>PE极值 ${peExt}</span>
        <span>PB偏离 ${fmtDev(row.pb_dev_pct)}</span><span>PB极值 ${pbExt}</span>
      </div>`}
      <div class="reason">${htmlEscape(row.reason || '')}${interest ? ' · ' + interest : ''}</div>
      <div class="muted" style="margin-top:8px">${row.self_engine ? `自算引擎 · ${row.self_algo || ''} · 对E大偏差 ${row.self_dev != null ? row.self_dev + '%' : '—'} · ${row.snapshot_date || ''} →` : '点击查看历史分位+本指数5y偏离兴趣区 →'}</div>
    </a>`;
  }).join('');
}

function stanceClass(stance) {
  if (stance.includes('主力买入')) return 'buy';
  if (stance.includes('左侧试探') || stance.includes('拐点')) return 'watch';
  if (stance.includes('高估')) return 'reduce';
  return '';
}

function barHtml(label, score) {
  const v = Math.max(0, Math.min(100, Number(score ?? 0)));
  const color = v >= 80 ? '#2ee6a8' : v >= 60 ? '#ffd166' : v >= 40 ? '#4da3ff' : '#ff7373';
  return `<div class="score-bar-row"><span>${label}</span><div class="score-bar"><i style="width:${v}%;background:${color}"></i></div><b>${score ?? '—'}</b></div>`;
}

function renderBatter(dash) {
  const list = document.getElementById('batterList');
  const meta = document.getElementById('batterMeta');
  if (!list) return;
  const rows = dash.rows || [];
  if (meta) {
    meta.textContent = `${rows.length} 个品种 · ${dash.generated_at || ''}`;
  }
  list.innerHTML = rows.map(row => `
    <a class="valuation-card ${stanceClass(row.stance || '')}" href="${detailHref(row.name)}" style="text-decoration:none;color:inherit;display:block">
      <div class="valuation-head">
        <b>${htmlEscape(row.name)}</b>
        <span class="tag ${stanceClass(row.stance || '')}">${htmlEscape(row.stance)}</span>
      </div>
      <div class="temperature"><strong>${row.total_score ?? '-'}</strong><span>击球分数</span></div>
      ${barHtml('估值', row.value_score)}
      ${barHtml('情绪', row.sentiment_score)}
      ${barHtml('动量', row.momentum_score)}
      <div class="reason" style="margin-top:8px">${htmlEscape(row.suggested_shares || '')}
        ${(row.momentum_detail?.turning_up) ? ' · 🔄 动量拐头向上' : ''}
        ${(row.sentiment_detail?.drawdown_pct != null) ? ` · 距250日高点 ${row.sentiment_detail.drawdown_pct}%` : ''}
        ${(row.momentum_detail?.mom_12_1_pct != null) ? ` · 12-1动量 ${row.momentum_detail.mom_12_1_pct}%` : ''}
      </div>
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

  // 估值数据源切换（自算 / 理杏仁 / 且慢）
  let valuationSource = 'self';
  const switchBox = document.getElementById('valuationSourceSwitch');
  if (switchBox) {
    switchBox.addEventListener('click', async (event) => {
      const btn = event.target.closest('.switch-btn');
      if (!btn || btn.dataset.source === valuationSource) return;
      valuationSource = btn.dataset.source;
      switchBox.querySelectorAll('.switch-btn').forEach(b => b.classList.toggle('active', b === btn));
      const meta = document.getElementById('valuationMeta');
      if (meta) meta.textContent = '切换中…';
      try {
        const dash = await getJSON('/api/valuations?source=' + valuationSource);
        renderValuations(dash);
      } catch (err) {
        if (meta) meta.textContent = '加载失败：' + (err && err.message || err);
      }
    });
  }

  let summary = null;
  try {
    summary = await getJSON('/api/summary');
    renderCards(summary);
    renderMarketPosition(summary.market_position || summary.ledger?.market_position);
    renderLedger(summary.ledger);
    renderActionSheet(summary.action_sheet);
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
    getJSON('/api/batter-score').then(renderBatter).catch(err => {
      const meta = document.getElementById('batterMeta');
      if (meta) meta.textContent = '击球分数加载失败';
      console.warn('batter-score', err);
    }),
    getJSON('/api/batter-backtest').then(d => {
      const box = document.getElementById('batterBacktest');
      if (!box || d.error) return;
      const h2h = d.head_to_head_3y || {};
      box.innerHTML = `
        <article class="topic-item">
          <div class="topic-source">📐 回测验证 · 8指数 × 20年（scripts/backtest_batter.py）</div>
          <h3>击球分数 vs 裸估值买入（3年持有期）</h3>
          <p>击球分数平均收益 <b style="color:#2ee6a8">${h2h.batter_score?.avg_ret ?? '-'}%</b>/胜率 ${h2h.batter_score?.win_rate ?? '-'}%
             vs 裸估值 ${h2h.baseline_value_only?.avg_ret ?? '-'}%/胜率 ${h2h.baseline_value_only?.win_rate ?? '-'}%</p>
          <p>典型案例：创业板指裸估值 3 年 -6.6%（胜率25%），击球分数 +33.4%（胜率67%）——情绪过滤成功避开"低估躺平"；恒生指数买入次数减半、收益反而更高。</p>
        </article>`;
    }).catch(() => {}),
    getJSON('/api/valuations').then(v => {
      if (!summary) renderValuations(v);
    }).catch(() => {}),
    getJSON('/api/decision').then(renderDecision).catch(() => {}),
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

// PWA: 注册 Service Worker（可添加到桌面当 APP 用）
if ('serviceWorker' in navigator) {
  window.addEventListener('load', () => {
    navigator.serviceWorker.register('/sw.js').catch(() => {});
  });
}
