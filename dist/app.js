const $ = (selector) => document.querySelector(selector);

const clamp = (value, min, max) => Math.max(min, Math.min(max, value));
const fmt = (value, digits = 1) => Number(value).toFixed(digits);

function temperatureColor(score) {
  if (score < 35) return '#60a5fa';
  if (score < 50) return '#a78bfa';
  if (score < 65) return '#5eead4';
  if (score < 80) return '#fbbf24';
  return '#fb7185';
}

function drawLineChart(target, rawValues, options = {}) {
  const values = rawValues.filter((d) => Number.isFinite(Number(d.value)));
  if (!values.length) {
    target.innerHTML = '<p class="chart-note">暂无可用曲线，等待下一次自动更新。</p>';
    return;
  }
  const width = 760;
  const height = options.height || 250;
  const pad = { left: 46, right: 16, top: 14, bottom: 28 };
  const ys = values.map((d) => Number(d.value));
  const thresholds = (options.thresholds || []).filter((d) => Number.isFinite(Number(d.value)));
  const rangeValues = ys.concat(thresholds.map((d) => Number(d.value)));
  let min = Math.min(...rangeValues);
  let max = Math.max(...rangeValues);
  const span = max - min || Math.max(Math.abs(max) * .1, 1);
  min -= span * .13;
  max += span * .13;
  const x = (i) => pad.left + i / Math.max(1, values.length - 1) * (width - pad.left - pad.right);
  const y = (v) => pad.top + (max - v) / (max - min) * (height - pad.top - pad.bottom);
  const path = values.map((d, i) => `${i ? 'L' : 'M'} ${x(i).toFixed(2)} ${y(Number(d.value)).toFixed(2)}`).join(' ');
  const area = `${path} L ${x(values.length - 1)} ${height - pad.bottom} L ${x(0)} ${height - pad.bottom} Z`;
  const ticks = [0, .25, .5, .75, 1].map((p) => ({ value: max - (max - min) * p, y: pad.top + (height - pad.top - pad.bottom) * p }));
  const dateIndexes = [0, Math.floor((values.length - 1) / 2), values.length - 1];
  const color = options.color || '#5eead4';
  target.innerHTML = `
    <svg viewBox="0 0 ${width} ${height}" role="img" aria-label="${options.label || '历史曲线'}">
      <defs><linearGradient id="areaGradient" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="${color}"/><stop offset="1" stop-color="${color}" stop-opacity="0"/></linearGradient></defs>
      ${ticks.map((t) => `<line class="grid-line" x1="${pad.left}" x2="${width-pad.right}" y1="${t.y}" y2="${t.y}"/><text class="axis-label" x="${pad.left-7}" y="${t.y+4}" text-anchor="end">${fmt(t.value, options.digits ?? 1)}</text>`).join('')}
      <path class="area-path" d="${area}" style="fill:url(#areaGradient)"/>
      <path class="line-path" d="${path}" style="stroke:${color}"/>
      ${thresholds.map((t) => `<line class="threshold-line" x1="${pad.left}" x2="${width-pad.right}" y1="${y(Number(t.value))}" y2="${y(Number(t.value))}" style="stroke:${t.color}"/><text class="threshold-label" x="${width-pad.right-4}" y="${y(Number(t.value))-5}" text-anchor="end" style="fill:${t.color}">${t.label}</text>`).join('')}
      <circle class="last-dot" cx="${x(values.length-1)}" cy="${y(ys.at(-1))}" r="5" style="stroke:${color}"/>
      ${dateIndexes.map((i) => `<text class="axis-label" x="${x(i)}" y="${height-7}" text-anchor="${i===0?'start':i===values.length-1?'end':'middle'}">${values[i].date.slice(5)}</text>`).join('')}
    </svg>`;
}

function change(values, sessions = 20) {
  if (values.length <= sessions) return 0;
  const start = Number(values.at(-sessions - 1).value);
  const end = Number(values.at(-1).value);
  return start ? (end / start - 1) * 100 : 0;
}

function renderGauge(t) {
  const score = Number(t.score);
  const color = temperatureColor(score);
  const trend = t.trend || { label: '方向未确认', tone: 'flat', message: t.explanation, shortDelta: 0, mediumDelta: 0, shortDirection: 'flat', mediumDirection: 'flat' };
  const arrows = { right: '→', left: '←', flat: '↔' };
  const deltaText = (value) => `${Number(value) >= 0 ? '+' : ''}${fmt(value, 1)}`;
  $('#temperature-score').textContent = fmt(score, 0);
  $('#temperature-title').textContent = `${t.label} · ${fmt(score, 0)}分`;
  $('#temperature-trend').className = `temperature-trend ${trend.tone}`;
  $('#temperature-trend').textContent = `${arrows[trend.shortDirection]} ${trend.label}`;
  $('#temperature-action').textContent = t.action;
  $('#temperature-action').style.color = color;
  $('#temperature-action').style.borderColor = `${color}66`;
  $('#temperature-action').style.background = `${color}16`;
  $('#plain-answer').textContent = trend.message;
  $('#gauge-fill').style.stroke = color;
  $('#gauge-fill').style.strokeDasharray = `${score} 100`;
  $('#gauge-needle').style.transform = `rotate(${score * 1.8 - 90}deg)`;
  $('#reference-row').innerHTML = [
    ['当前', fmt(score, 0), ''],
    ['5日趋势', `${arrows[trend.shortDirection]} ${deltaText(trend.shortDelta)}`, trend.shortDirection],
    ['20日趋势', `${arrows[trend.mediumDirection]} ${deltaText(trend.mediumDelta)}`, trend.mediumDirection],
    ['记录区间', `${fmt(t.rangeLow,0)}–${fmt(t.rangeHigh,0)}`, ''],
  ].map(([label, value, direction]) => `<div class="reference ${direction}"><span>${label}</span><strong>${value}</strong></div>`).join('');
  $('#decision-title').textContent = `现在更适合：${t.action}`;
  $('#decision-copy').textContent = t.explanation;
  $('#strongest-driver').textContent = t.strongest;
  $('#weakest-driver').textContent = t.weakest;
  $('#action-ladder').innerHTML = t.bands.map((band) => `<div class="ladder-step ${score >= band.from && score < band.to ? 'active' : ''}">${band.label}</div>`).join('');
}

function renderTemperatureChart(t) {
  const target = $('#temperature-chart');
  const redraw = (range) => drawLineChart(target, t.history.slice(-range), { label: '综合宏观温度历史曲线', color: temperatureColor(t.score), digits: 0 });
  redraw(60);
  document.querySelectorAll('[data-chart="temperature-chart"] button').forEach((button) => {
    button.addEventListener('click', () => {
      button.parentElement.querySelectorAll('button').forEach((b) => b.classList.remove('active'));
      button.classList.add('active');
      redraw(Number(button.dataset.range));
    });
  });
}

function renderComponents(components) {
  $('#components').innerHTML = components.map((item) => `
    <article class="component">
      <div class="component-head"><span>${item.name}</span><strong>${fmt(item.score, 0)}</strong></div>
      <div class="bar"><span style="width:${clamp(item.score, 0, 100)}%;background:${temperatureColor(item.score)}"></span></div>
      <p>${item.reading}</p>
      <small>权重 ${item.weight}%</small>
    </article>`).join('');
}

function renderLiquidity(liquidity, series) {
  if (!liquidity?.indicators?.length) return;
  const signed = (value, digits = 2) => `${Number(value) >= 0 ? '+' : ''}${fmt(value, digits)}`;
  $('#liquidity-score').innerHTML = `<strong>${fmt(liquidity.score, 0)}</strong><span>/ 100 · ${liquidity.label}</span>`;
  $('#liquidity-summary').textContent = liquidity.summary;
  $('#liquidity-grid').innerHTML = liquidity.indicators.map((item) => `
    <article class="liquidity-card">
      <div class="liquidity-card-head">
        <div>
          <span class="liquidity-state ${item.tone}">${item.status}</span>
          <h3>${item.name}</h3>
          <p>${item.meaning}</p>
        </div>
        <table class="liquidity-mini-table" aria-label="${item.name}关键数据">
          <tr><th>最新</th><td>${fmt(item.value, item.key === 'nfci' ? 3 : 2)}${item.unit}</td></tr>
          <tr><th>20期变化</th><td>${signed(item.change20, item.key === 'nfci' ? 3 : 2)}${item.unit}</td></tr>
          <tr class="risk-row"><th>风险线</th><td>≥ ${fmt(item.riskLine, item.key === 'nfci' ? 2 : 1)}</td></tr>
          <tr class="opportunity-row"><th>机会线</th><td>≤ ${fmt(item.opportunityLine, item.key === 'nfci' ? 2 : 1)}</td></tr>
        </table>
      </div>
      <p class="liquidity-reading">${item.interpretation}</p>
      <div class="threshold-signal ${item.value >= item.riskLine ? 'risk' : item.value <= item.opportunityLine ? 'opportunity' : 'neutral'}">${item.thresholdSignal}</div>
      <div class="liquidity-signal opportunity"><span>机会</span><p>${item.opportunity}</p></div>
      <div class="liquidity-signal risk"><span>风险</span><p>${item.risk}</p></div>
      <div class="liquidity-chart" id="liquidity-chart-${item.key}"></div>
      <div class="liquidity-foot"><span>友好度 ${fmt(item.score, 0)}分</span><a href="${series[item.key]?.sourceUrl || '#'}" target="_blank" rel="noopener">FRED 原始数据</a></div>
    </article>`).join('');
  const colors = { effr: '#fbbf24', real10y: '#fb7185', broadDollar: '#60a5fa', nfci: '#4ade80' };
  liquidity.indicators.forEach((item) => {
    const values = series[item.key]?.values || [];
    const range = item.key === 'nfci' ? 104 : 180;
    drawLineChart($(`#liquidity-chart-${item.key}`), values.slice(-range), {
      height: 150,
      label: `${item.name}历史曲线`,
      color: colors[item.key],
      digits: item.key === 'nfci' ? 2 : 2,
      thresholds: [
        { value: item.riskLine, label: `风险线 ${fmt(item.riskLine, item.key === 'nfci' ? 2 : 1)}`, color: '#fb7185' },
        { value: item.opportunityLine, label: `机会线 ${fmt(item.opportunityLine, item.key === 'nfci' ? 2 : 1)}`, color: '#4ade80' },
      ],
    });
  });
}

function renderMiniCharts(series) {
  const configs = [
    ['turnover', 80, '#5eead4'],
    ['us10y', 120, '#fb7185'],
    ['oil', 120, '#fbbf24'],
    ['gold', 120, '#a78bfa'],
    ['copper', 120, '#60a5fa'],
    ['dollar', 120, '#94a3b8'],
  ];
  $('#chart-grid').innerHTML = configs.map(([key]) => `<article class="mini-chart"><div class="mini-head"><div><h3>${series[key]?.label || key}</h3><span>${series[key]?.meaning || ''}</span></div><div class="mini-value" id="value-${key}"></div></div><div id="chart-${key}"></div></article>`).join('');
  configs.forEach(([key, range, color]) => {
    const item = series[key];
    if (!item?.values?.length) return;
    const values = item.values.slice(-range);
    const last = Number(values.at(-1).value);
    const delta = change(values, 20);
    $(`#value-${key}`).innerHTML = `${fmt(last, key === 'turnover' ? 2 : 2)} <small>${item.unit}</small><span class="mini-change">20日 ${delta >= 0 ? '+' : ''}${fmt(delta, 1)}%</span>`;
    drawLineChart($(`#chart-${key}`), values, { height: 150, label: `${item.label}历史曲线`, color, digits: key === 'turnover' ? 1 : 2 });
  });
}

function renderChina(china) {
  const cards = [
    ['工业增加值', china.industrial, '1月至当前月份累计同比', '生产韧性'],
    ['社会消费品零售', china.retail, '1月至当前月份累计同比', '内需温度'],
    ['固定资产投资', china.fixedAsset, '1月至当前月份累计同比', '投资动能'],
    ['房地产开发投资', china.realEstate, '1月至当前月份累计同比', '地产拖累'],
    ['高技术制造业', china.highTechMonthly, '当月同比', '新动能'],
    ['工业企业利润', china.industrialProfit, `累计至 ${china.profitPeriod}`, '盈利修复'],
  ];
  $('#macro-grid').innerHTML = cards.map(([label, value, context, tag]) => `
    <article class="macro-card">
      <div class="label">${label}</div>
      <div class="value" style="color:${value >= 0 ? '#f3f7fb' : '#fb7185'}">${value >= 0 ? '+' : ''}${fmt(value, 1)}%</div>
      <div class="context">${context} · ${tag}</div>
    </article>`).join('');
  const profitLag = china.profitPeriod !== china.period;
  $('#release-note').innerHTML = `最新月度运行数据：<strong>${china.period}</strong>。${profitLag ? `工业企业利润仍只公布至 <strong>${china.profitPeriod}</strong>，这是官方发布时间差异，并非漏更新。` : '各项指标已更新至同一月份。'} <a href="${china.sourceUrl}" target="_blank" rel="noopener">查看国家统计局原始发布</a>`;
}

async function start() {
  try {
    const response = await fetch(`data.json?v=${Date.now()}`, { cache: 'no-store' });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const data = await response.json();
    const updated = new Date(data.meta.updatedAt);
    $('#freshness').textContent = `自动更新 · ${updated.toLocaleString('zh-CN', { hour12: false })}`;
    if (data.meta.status !== 'ok') $('#freshness').textContent += ' · 部分来源暂未刷新';
    renderGauge(data.temperature);
    renderTemperatureChart(data.temperature);
    renderComponents(data.temperature.components);
    renderLiquidity(data.liquidity, data.series);
    renderMiniCharts(data.series);
    renderChina(data.china);
  } catch (error) {
    $('#freshness').textContent = '数据读取失败，请稍后刷新';
    $('#plain-answer').textContent = '页面框架正常，但本次没有读到数据文件。';
    console.error(error);
  }
}

start();
