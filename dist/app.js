const $ = (selector) => document.querySelector(selector);

const clamp = (value, min, max) => Math.max(min, Math.min(max, value));
const fmt = (value, digits = 1) => Number(value).toFixed(digits);
const esc = (value) => String(value ?? '').replace(/[&<>"']/g, (char) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[char]));
let chartSerial = 0;

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
  const timeSpanDays = (new Date(values.at(-1).date) - new Date(values[0].date)) / 86400000;
  const dateLabel = (day) => timeSpanDays > 370 ? day.slice(0, 7) : day.slice(5);
  const color = options.color || '#5eead4';
  const gradientId = `areaGradient-${++chartSerial}`;
  const eventColors = { risk: '#fb7185', opportunity: '#4ade80', info: '#60a5fa' };
  const eventMarkers = (options.events || []).map((event, eventIndex) => {
    if (event.date < values[0].date || event.date > values.at(-1).date) return '';
    let pointIndex = values.findIndex((item) => item.date >= event.date);
    if (pointIndex < 0) pointIndex = values.length - 1;
    const pointValue = Number(values[pointIndex].value);
    const markerColor = eventColors[event.type] || eventColors.info;
    return `<g class="event-marker" data-event-index="${eventIndex}" role="button" tabindex="0" aria-label="${event.date} ${event.title}">
      <line x1="${x(pointIndex)}" x2="${x(pointIndex)}" y1="${y(pointValue)-18}" y2="${y(pointValue)-5}" style="stroke:${markerColor}"/>
      <circle cx="${x(pointIndex)}" cy="${y(pointValue)}" r="7" style="fill:${markerColor}"/>
      <circle cx="${x(pointIndex)}" cy="${y(pointValue)}" r="3" class="event-marker-core"/>
    </g>`;
  }).join('');
  target.innerHTML = `
    <svg viewBox="0 0 ${width} ${height}" role="img" aria-label="${options.label || '历史曲线'}">
      <defs><linearGradient id="${gradientId}" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="${color}"/><stop offset="1" stop-color="${color}" stop-opacity="0"/></linearGradient></defs>
      ${ticks.map((t) => `<line class="grid-line" x1="${pad.left}" x2="${width-pad.right}" y1="${t.y}" y2="${t.y}"/><text class="axis-label" x="${pad.left-7}" y="${t.y+4}" text-anchor="end">${fmt(t.value, options.digits ?? 1)}</text>`).join('')}
      <path class="area-path" d="${area}" style="fill:url(#${gradientId})"/>
      <path class="line-path" d="${path}" style="stroke:${color}"/>
      ${thresholds.map((t) => `<line class="threshold-line" x1="${pad.left}" x2="${width-pad.right}" y1="${y(Number(t.value))}" y2="${y(Number(t.value))}" style="stroke:${t.color}"/><text class="threshold-label" x="${width-pad.right-4}" y="${y(Number(t.value))-5}" text-anchor="end" style="fill:${t.color}">${t.label}</text>`).join('')}
      ${eventMarkers}
      <circle class="last-dot" cx="${x(values.length-1)}" cy="${y(ys.at(-1))}" r="5" style="stroke:${color}"/>
      ${dateIndexes.map((i) => `<text class="axis-label" x="${x(i)}" y="${height-7}" text-anchor="${i===0?'start':i===values.length-1?'end':'middle'}">${dateLabel(values[i].date)}</text>`).join('')}
    </svg>`;
  if (options.onEventClick) {
    target.querySelectorAll('.event-marker').forEach((marker) => {
      const open = (event) => {
        event.stopPropagation();
        options.onEventClick((options.events || [])[Number(marker.dataset.eventIndex)]);
      };
      marker.addEventListener('click', open);
      marker.addEventListener('keydown', (event) => {
        if (event.key === 'Enter' || event.key === ' ') open(event);
      });
    });
  }
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

function sectorSnapshot(item, offset = 0) {
  const rows = item.history || [];
  if (rows.length < 21) {
    return {
      date: item.date, value: item.value, change5: Number(item.change5 || 0), change20: Number(item.change20 || 0),
      score: Number(item.signalScore ?? 50), shift: Number(item.momentumShift || 0), strength: Number(item.strength || 40),
      state: item.state || '观察', tone: item.tone || 'observe',
    };
  }
  const index = clamp(rows.length - 1 + Number(offset), 20, rows.length - 1);
  const value = Number(rows[index].value);
  const pct = (sessions) => {
    const base = Number(rows[index - sessions].value);
    return base ? (value / base - 1) * 100 : 0;
  };
  const change5 = pct(5);
  const change20 = pct(20);
  const shift = change5 - change20 / 4;
  const crowdingPenalty = Math.max(0, change5 - 8) * 5 + Math.max(0, change20 - 12) * 3;
  const score = clamp(Number(item.frameworkScore ?? 50) + clamp(change5, -8, 8) * 1.6 + clamp(change20, -12, 12) * .6 - crowdingPenalty, 0, 100);
  const strength = clamp(30 + Math.abs(score - 50) * 1.4 + Math.abs(shift) * 2, 25, 100);
  const tone = score <= 37 ? 'risk' : score >= 63 ? 'opportunity' : 'observe';
  const state = tone === 'risk' ? '风险' : tone === 'opportunity' ? '机会' : '观察';
  return { date: rows[index].date, value, change5, change20, score, shift, strength, state, tone };
}

function openSectorDialog(item, snapshot) {
  const dialog = $('#sector-dialog');
  const state = $('#sector-dialog-state');
  state.className = `event-dialog-type ${snapshot.tone}`;
  state.textContent = `${snapshot.state} · 信号强度 ${fmt(snapshot.strength, 0)}`;
  $('#sector-dialog-title').textContent = item.name;
  $('#sector-dialog-meta').textContent = `${snapshot.date} · ${item.role} · ${item.proxy}`;
  $('#sector-dialog-thesis').textContent = item.thesis;
  $('#sector-dialog-metrics').innerHTML = [
    ['代理价格', fmt(snapshot.value, 3)],
    ['5日变化', `${snapshot.change5 >= 0 ? '+' : ''}${fmt(snapshot.change5, 1)}%`],
    ['20日变化', `${snapshot.change20 >= 0 ? '+' : ''}${fmt(snapshot.change20, 1)}%`],
    ['机会位置', `${fmt(snapshot.score, 0)} / 100`],
  ].map(([label, value]) => `<div><span>${label}</span><strong>${value}</strong></div>`).join('');
  $('#sector-dialog-validation').textContent = item.validation;
  $('#sector-dialog-risk').textContent = item.risk || '等待基本面与资金进一步验证';
  $('#sector-dialog-reading').textContent = snapshot.tone === 'opportunity'
    ? '位置和趋势相对有利，但仍需满足机会确认条件，不能只看价格。'
    : snapshot.tone === 'risk'
      ? '当前风险信号较强，优先等待拥挤度、估值或基本面风险释放。'
      : '方向尚未形成共振，维持观察比提前下注更重要。';
  if (typeof dialog.showModal === 'function') dialog.showModal();
  else dialog.setAttribute('open', '');
}

function renderSectorMap(sectors) {
  const target = $('#sector-scatter');
  const slider = $('#sector-map-time');
  const dateLabel = $('#sector-map-date');
  const playButton = $('#sector-map-play');
  if (!target || !sectors?.length) return;
  let offset = Number(slider.value || 0);
  let timer = null;
  const shortNames = { optical: '光通信', innovativeDrug: '创新药', aiApplication: 'AI应用', aiHardware: 'AI硬件', chemical: '化工', copper: '铜有色', broad: '沪深300', robot: '机器人', space: '航天' };

  const draw = () => {
    const width = Math.max(300, Math.round(target.clientWidth || 640));
    const height = clamp(Math.round(width * .56), 290, 380);
    const pad = { left: 42, right: 20, top: 20, bottom: 38 };
    const plotWidth = width - pad.left - pad.right;
    const plotHeight = height - pad.top - pad.bottom;
    const x = (value) => pad.left + clamp(value, 0, 100) / 100 * plotWidth;
    const y = (value) => pad.top + (12 - clamp(value, -12, 12)) / 24 * plotHeight;
    const radius = (value) => 8 + (clamp(value, 25, 100) - 25) / 75 * (width < 430 ? 9 : 13);
    const snapshots = sectors.map((item, index) => ({ item, index, now: sectorSnapshot(item, offset), before: sectorSnapshot(item, offset - 5) }));
    const compact = width < 430;
    const compactLabels = new Set([
      ...snapshots.filter(({ now }) => now.tone === 'risk').map(({ item }) => item.key),
      ...snapshots.filter(({ now }) => now.tone !== 'risk').sort((a, b) => b.now.strength - a.now.strength).slice(0, 4).map(({ item }) => item.key),
    ]);
    dateLabel.textContent = snapshots[0]?.now.date ? `${snapshots[0].now.date} · ${offset === 0 ? '最新' : `${Math.abs(offset)}个交易日前`}` : '最新';
    const trails = snapshots.map(({ now, before }) => `
      <line class="scatter-trail ${now.tone}" x1="${x(before.score)}" y1="${y(before.shift)}" x2="${x(now.score)}" y2="${y(now.shift)}"/>
      <circle class="scatter-prior ${now.tone}" cx="${x(before.score)}" cy="${y(before.shift)}" r="3"/>`).join('');
    const bubbles = snapshots.slice().sort((a, b) => b.now.strength - a.now.strength).map(({ item, index, now }, rank) => {
      const r = radius(now.strength);
      const labelY = (compact ? rank : index) % 2 === 0 ? -r - 5 : r + 13;
      const labelX = compact ? ((rank % 3) - 1) * 7 : 0;
      const name = shortNames[item.key] || item.name;
      return `<g class="sector-bubble ${now.tone}" data-sector-key="${item.key}" role="button" tabindex="0" aria-label="${item.name}，${now.state}，信号强度${fmt(now.strength, 0)}">
        <title>${item.name} · ${now.state} · 5日 ${now.change5 >= 0 ? '+' : ''}${fmt(now.change5, 1)}% · 20日 ${now.change20 >= 0 ? '+' : ''}${fmt(now.change20, 1)}%</title>
        <circle cx="${x(now.score)}" cy="${y(now.shift)}" r="${r}" opacity=".84"/>
        ${!compact || compactLabels.has(item.key) ? `<text x="${x(now.score) + labelX}" y="${y(now.shift) + labelY}" text-anchor="middle">${name}</text>` : ''}
      </g>`;
    }).join('');
    target.innerHTML = `<svg viewBox="0 0 ${width} ${height}" role="img" aria-label="板块机会、风险与趋势散点图">
      <rect class="scatter-frame" x="${pad.left}" y="${pad.top}" width="${plotWidth}" height="${plotHeight}" rx="10"/>
      <rect class="scatter-band risk" x="${x(0)}" y="${pad.top}" width="${x(37)-x(0)}" height="${plotHeight}"/>
      <rect class="scatter-band observe" x="${x(37)}" y="${pad.top}" width="${x(63)-x(37)}" height="${plotHeight}"/>
      <rect class="scatter-band opportunity" x="${x(63)}" y="${pad.top}" width="${x(100)-x(63)}" height="${plotHeight}"/>
      <line class="scatter-boundary" x1="${x(37)}" x2="${x(37)}" y1="${pad.top}" y2="${height-pad.bottom}"/>
      <line class="scatter-boundary" x1="${x(63)}" x2="${x(63)}" y1="${pad.top}" y2="${height-pad.bottom}"/>
      <line class="scatter-grid" x1="${pad.left}" x2="${width-pad.right}" y1="${y(0)}" y2="${y(0)}"/>
      <text class="scatter-axis-title" x="${x(18.5)}" y="${height-13}" text-anchor="middle">风险</text>
      <text class="scatter-axis-title" x="${x(50)}" y="${height-13}" text-anchor="middle">观察</text>
      <text class="scatter-axis-title" x="${x(81.5)}" y="${height-13}" text-anchor="middle">机会</text>
      <text class="scatter-axis-label" x="${pad.left-7}" y="${pad.top+8}" text-anchor="end">改善</text>
      <text class="scatter-axis-label" x="${pad.left-7}" y="${height-pad.bottom}" text-anchor="end">转弱</text>
      ${trails}${bubbles}
    </svg>`;
    target.querySelectorAll('.sector-bubble').forEach((bubble) => {
      const item = sectors.find((candidate) => candidate.key === bubble.dataset.sectorKey);
      const open = () => openSectorDialog(item, sectorSnapshot(item, offset));
      bubble.addEventListener('click', open);
      bubble.addEventListener('keydown', (event) => { if (event.key === 'Enter' || event.key === ' ') open(); });
    });
  };
  const stop = () => {
    if (timer) clearInterval(timer);
    timer = null;
    playButton.textContent = '播放20日轨迹';
  };
  slider.addEventListener('input', () => { offset = Number(slider.value); stop(); draw(); });
  playButton.addEventListener('click', () => {
    if (timer) { stop(); return; }
    if (offset >= 0) offset = -20;
    slider.value = offset;
    draw();
    playButton.textContent = '暂停';
    timer = setInterval(() => {
      offset += 1;
      slider.value = offset;
      draw();
      if (offset >= 0) stop();
    }, 420);
  });
  draw();
  if ('ResizeObserver' in window) {
    let priorWidth = target.clientWidth;
    new ResizeObserver(() => {
      if (Math.abs(target.clientWidth - priorWidth) > 4) { priorWidth = target.clientWidth; draw(); }
    }).observe(target);
  }
}

function openEventDialog(item) {
  if (!item) return;
  const dialog = $('#event-dialog');
  $('#event-dialog-type').textContent = item.type === 'risk' ? '风险变化' : item.type === 'opportunity' ? '机会变化' : '重要变化';
  $('#event-dialog-type').className = `event-dialog-type ${item.type}`;
  $('#event-dialog-title').textContent = item.title;
  $('#event-dialog-date').textContent = `${item.date} · ${item.indicator} · 当日温度 ${fmt(item.score, 1)}分`;
  $('#event-dialog-detail').textContent = item.detail;
  $('#event-dialog-impact').textContent = item.impact;
  const source = $('#event-dialog-source');
  source.hidden = !item.sourceUrl;
  if (item.sourceUrl) source.href = item.sourceUrl;
  if (typeof dialog.showModal === 'function') dialog.showModal();
  else dialog.setAttribute('open', '');
}

function renderEventTimeline(events) {
  const target = $('#event-timeline');
  if (!target) return;
  if (!events?.length) {
    target.innerHTML = '<p class="chart-note">当前窗口没有需要特别标记的事件。</p>';
    return;
  }
  target.innerHTML = events.slice().reverse().map((item) => `
    <button class="event-chip ${item.type}" type="button" data-event-id="${item.id}">
      <span>${item.date.slice(5)}</span><strong>${item.title}</strong><small>${item.indicator}</small>
    </button>`).join('');
  target.querySelectorAll('.event-chip').forEach((button) => {
    button.addEventListener('click', () => openEventDialog(events.find((item) => item.id === button.dataset.eventId)));
  });
}

function openMarketEventDialog(group) {
  if (!group?.items?.length) return;
  const dialog = $('#market-event-dialog');
  const importanceLabels = { high: '高影响', medium: '中影响', low: '低影响' };
  const statusLabels = { past: '已发生', today: '今天', future: '未来日程' };
  const importance = group.importance || 'medium';
  const type = $('#market-event-dialog-type');
  type.className = `event-dialog-type importance-${importance}`;
  type.textContent = `${importanceLabels[importance]} · ${statusLabels[group.status] || '事件'}`;
  $('#market-event-dialog-title').textContent = group.items.length > 1 ? `${group.date} · ${group.items.length}项事件` : group.items[0].title;
  $('#market-event-dialog-date').textContent = `${group.date} · ${group.items.map((item) => item.category).filter(Boolean).join(' / ')}`;
  $('#market-event-dialog-items').innerHTML = group.items.map((item) => {
    const safeUrl = /^https:\/\//.test(item.sourceUrl || '') ? item.sourceUrl : '';
    return `<article class="market-event-dialog-item">
      <div><span>${esc(item.category || '市场事件')}</span><small>${esc(item.time || '')}</small></div>
      <h3>${esc(item.title)}</h3>
      <p>${esc(item.detail || '')}</p>
      <div class="event-dialog-impact"><span>对市场意味着什么</span><strong>${esc(item.impact || '')}</strong></div>
      ${safeUrl ? `<a href="${esc(safeUrl)}" target="_blank" rel="noopener">查看官方/原始日程</a>` : ''}
    </article>`;
  }).join('');
  if (typeof dialog.showModal === 'function') dialog.showModal();
  else dialog.setAttribute('open', '');
}

function renderGlobalEventAxis(events, asOf) {
  const target = $('#market-event-axis');
  if (!target) return;
  const today = new Date(`${(asOf || new Date().toISOString()).slice(0, 10)}T00:00:00Z`);
  const dayMs = 86400000;
  const spanDays = 90;
  const ranking = { low: 1, medium: 2, high: 3 };
  const groupsByDate = new Map();
  (events || []).forEach((item) => {
    const eventDate = new Date(`${item.date}T00:00:00Z`);
    if (Number.isNaN(eventDate.getTime())) return;
    const delta = Math.round((eventDate - today) / dayMs);
    if (delta < -spanDays || delta > spanDays) return;
    if (!groupsByDate.has(item.date)) groupsByDate.set(item.date, []);
    groupsByDate.get(item.date).push(item);
  });
  const groups = [...groupsByDate.entries()].sort(([a], [b]) => a.localeCompare(b)).map(([date, items]) => {
    const importance = items.reduce((best, item) => ranking[item.importance] > ranking[best] ? item.importance : best, 'low');
    const eventDate = new Date(`${date}T00:00:00Z`);
    const delta = Math.round((eventDate - today) / dayMs);
    return { date, items, importance, delta, status: delta < 0 ? 'past' : delta > 0 ? 'future' : 'today' };
  });
  if (!groups.length) {
    target.innerHTML = '<p class="chart-note">当前前后90天没有已确认的重要事件。</p>';
    return;
  }

  const width = 1040;
  const height = 250;
  const axisY = 126;
  const centerX = width / 2;
  const sideWidth = centerX - 46;
  const positionX = (delta) => delta < 0
    ? centerX - Math.min(Math.abs(delta), spanDays) / spanDays * sideWidth
    : centerX + Math.min(delta, spanDays) / spanDays * sideWidth;
  const colors = { high: '#fb7185', medium: '#fbbf24', low: '#60a5fa' };
  const radii = { high: 9, medium: 7, low: 5.5 };
  const laneUsage = { top: [-999, -999, -999], bottom: [-999, -999, -999] };
  const assignLane = (x, side) => {
    const lanes = laneUsage[side];
    let lane = lanes.findIndex((last) => x - last >= 92);
    if (lane < 0) lane = lanes.indexOf(Math.min(...lanes));
    lanes[lane] = x;
    return lane;
  };
  const pointMarkup = groups.map((group, index) => {
    const x = positionX(group.delta);
    const side = index % 2 === 0 ? 'top' : 'bottom';
    const lane = assignLane(x, side);
    const labelY = side === 'top' ? 32 + lane * 25 : 181 + lane * 24;
    const stemEnd = side === 'top' ? labelY + 10 : labelY - 19;
    const title = group.items.length > 1 ? `${group.items.length}项事件` : group.items[0].title;
    const shortTitle = title.length > 10 ? `${title.slice(0, 10)}…` : title;
    const color = colors[group.importance];
    const radius = radii[group.importance] + Math.min(2, group.items.length - 1);
    return `<g class="market-event-point importance-${group.importance}" data-event-index="${index}" role="button" tabindex="0" aria-label="${esc(group.date)} ${esc(title)}">
      <line x1="${x}" x2="${x}" y1="${axisY}" y2="${stemEnd}" style="stroke:${color}"/>
      <circle cx="${x}" cy="${axisY}" r="${radius + 4}" class="event-hit"/>
      <circle cx="${x}" cy="${axisY}" r="${radius}" style="fill:${color}"/>
      <circle cx="${x}" cy="${axisY}" r="${Math.max(2, radius - 4)}" class="event-core"/>
      <text x="${x}" y="${labelY}" text-anchor="middle" class="market-event-label">${esc(shortTitle)}</text>
      <text x="${x}" y="${labelY + 13}" text-anchor="middle" class="market-event-date">${esc(group.date.slice(5))}${group.items.length > 1 ? ` · ×${group.items.length}` : ''}</text>
    </g>`;
  }).join('');
  target.innerHTML = `<div class="market-axis-scroll"><svg viewBox="0 0 ${width} ${height}" role="img" aria-label="过去90天到未来90天全球市场事件轴">
    <rect x="36" y="103" width="${centerX - 36}" height="46" rx="18" class="axis-past-zone"/>
    <rect x="${centerX}" y="103" width="${centerX - 36}" height="46" rx="18" class="axis-future-zone"/>
    <line x1="36" x2="${width - 36}" y1="${axisY}" y2="${axisY}" class="market-axis-line"/>
    <line x1="${centerX}" x2="${centerX}" y1="18" y2="232" class="market-today-line"/>
    <text x="42" y="96" class="market-axis-side" text-anchor="start">过去90天 · 已发生</text>
    <text x="${width - 42}" y="96" class="market-axis-side" text-anchor="end">未来90天 · 已确认日程</text>
    <rect x="${centerX - 30}" y="109" width="60" height="34" rx="17" class="market-today-pill"/>
    <text x="${centerX}" y="131" text-anchor="middle" class="market-today-text">今天</text>
    ${pointMarkup}
  </svg></div>`;
  target.querySelectorAll('.market-event-point').forEach((point) => {
    const open = () => openMarketEventDialog(groups[Number(point.dataset.eventIndex)]);
    point.addEventListener('click', open);
    point.addEventListener('keydown', (event) => {
      if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); open(); }
    });
  });
  const scroll = target.querySelector('.market-axis-scroll');
  requestAnimationFrame(() => { scroll.scrollLeft = Math.max(0, scroll.scrollWidth / 2 - scroll.clientWidth / 2); });
}

function renderTemperatureChart(t, events = []) {
  const target = $('#temperature-chart');
  const history = t.history || [];
  const riskLine = 45;
  const opportunityLine = 60;
  let range = Math.min(60, history.length);
  let end = history.length;
  const redraw = () => {
    const start = Math.max(0, end - range);
    const visible = history.slice(start, end);
    drawLineChart(target, visible, {
      label: '综合宏观温度历史曲线',
      color: temperatureColor(t.score),
      digits: 0,
      thresholds: [
        { value: riskLine, label: '风险线 45', color: '#fb7185' },
        { value: opportunityLine, label: '机会线 60', color: '#4ade80' },
      ],
    });
    if (visible.length) $('#timeline-window').textContent = `${visible[0].date} 至 ${visible.at(-1).date} · 按住曲线左右拖动`;
  };
  redraw();
  const score = Number(t.score);
  const zone = score < riskLine ? '风险·防守' : score >= opportunityLine ? '机会·确认' : '均衡·观察';
  const zoneTarget = $('#temperature-zone-current');
  if (zoneTarget) zoneTarget.textContent = `当前 ${fmt(score, 0)}分 · ${zone}`;
  document.querySelectorAll('[data-chart="temperature-chart"] button').forEach((button) => {
    button.addEventListener('click', () => {
      button.parentElement.querySelectorAll('button').forEach((b) => b.classList.remove('active'));
      button.classList.add('active');
      range = Math.min(Number(button.dataset.range), history.length);
      end = history.length;
      redraw();
    });
  });
  const moveWindow = (steps) => {
    end = clamp(end + steps, range, history.length);
    redraw();
  };
  $('#timeline-earlier').addEventListener('click', () => moveWindow(-Math.max(5, Math.round(range / 4))));
  $('#timeline-later').addEventListener('click', () => moveWindow(Math.max(5, Math.round(range / 4))));
  let dragging = false;
  let startX = 0;
  let startEnd = end;
  target.addEventListener('pointerdown', (event) => {
    dragging = true;
    startX = event.clientX;
    startEnd = end;
    target.classList.add('dragging');
    target.setPointerCapture?.(event.pointerId);
  });
  target.addEventListener('pointermove', (event) => {
    if (!dragging) return;
    const shift = Math.round((startX - event.clientX) / Math.max(target.clientWidth, 1) * range);
    end = clamp(startEnd + shift, range, history.length);
    redraw();
  });
  const stopDrag = () => { dragging = false; target.classList.remove('dragging'); };
  target.addEventListener('pointerup', stopDrag);
  target.addEventListener('pointercancel', stopDrag);
}

function renderDailyReport(report) {
  if (!report) return;
  $('#report-date').textContent = `${report.date} · ${report.publishTime}`;
  $('#decision-title').textContent = report.title;
  $('#decision-copy').textContent = report.summary;
  $('#report-bottom-line').textContent = report.bottomLine;
  $('#strongest-driver').textContent = report.support;
  $('#weakest-driver').textContent = report.drag;
  $('#report-evidence').innerHTML = report.evidence.map((item) => `
    <article class="evidence-item ${item.tone}">
      <span>${item.label}</span><strong>${item.value}</strong><p>${item.context}</p>
    </article>`).join('');
}

function renderComponents(components) {
  $('#components').innerHTML = components.map((item) => `
    <article class="component decision-thermometer ${item.tone || 'observe'}">
      <div class="component-head">
        <div><span>${item.name}</span><strong>${item.reading}</strong></div>
        <b class="component-zone">${item.zone || '观察'}</b>
      </div>
      <div class="decision-scale" role="img" aria-label="${item.name}：当前处于${item.zone || '观察'}区">
        <div class="decision-zones"><i></i><i></i><i></i></div>
        <span class="decision-threshold cautious-threshold" aria-hidden="true"></span>
        <span class="decision-threshold bold-threshold" aria-hidden="true"></span>
        <span class="decision-marker" style="left:${clamp(item.position ?? item.score ?? 50, 2, 98)}%"><b>当前</b></span>
      </div>
      <div class="decision-labels"><span>谨慎</span><span>观察</span><span>大胆</span></div>
      <div class="threshold-values">
        <span class="cautious">${item.cautiousLabel || '谨慎线 ≤ 35'}</span>
        <span class="bold">${item.boldLabel || '大胆线 ≥ 65'}</span>
      </div>
      <p class="component-interpretation">${item.interpretation || ''}</p>
      <small>${item.percentile || '等待历史分位数据'}</small>
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
          <tr><th>数据日</th><td>${series[item.key]?.values?.at(-1)?.date || '—'}</td></tr>
          <tr><th>20期变化</th><td>${signed(item.change20, item.key === 'nfci' ? 3 : 2)}${item.unit}</td></tr>
          <tr class="risk-row"><th>风险线</th><td>≥ ${fmt(item.riskLine, item.key === 'nfci' ? 2 : 1)}</td></tr>
          <tr class="opportunity-row"><th>机会线</th><td>≤ ${fmt(item.opportunityLine, item.key === 'nfci' ? 2 : 1)}</td></tr>
        </table>
      </div>
      <p class="liquidity-reading">${item.interpretation}</p>
      <div class="threshold-signal ${item.value >= item.riskLine ? 'risk' : item.value <= item.opportunityLine ? 'opportunity' : 'neutral'}">${item.thresholdSignal}</div>
      <div class="liquidity-signal opportunity"><span>机会</span><p>${item.opportunity}</p></div>
      <div class="liquidity-signal risk"><span>风险</span><p>${item.risk}</p></div>
      <div class="liquidity-chart-head">
        <span id="liquidity-range-label-${item.key}">历史区间</span>
        <div class="liquidity-range-buttons" data-key="${item.key}">
          ${item.key === 'nfci'
            ? '<button type="button" data-span="5y">5年</button><button class="active" type="button" data-span="max">全部</button>'
            : '<button type="button" data-span="1y">1年</button><button class="active" type="button" data-span="5y">5年</button>'}
        </div>
      </div>
      <div class="liquidity-chart" id="liquidity-chart-${item.key}"></div>
      <div class="liquidity-foot"><span>当前判断：${item.status}</span><a href="${series[item.key]?.sourceUrl || '#'}" target="_blank" rel="noopener">FRED 原始数据</a></div>
    </article>`).join('');
  const colors = { effr: '#fbbf24', real10y: '#fb7185', broadDollar: '#60a5fa', nfci: '#4ade80' };
  const drawLiquidityChart = (item, span) => {
    const source = series[item.key] || {};
    const recent = source.values || [];
    const long = source.longValues?.length ? source.longValues : recent;
    let values;
    if (span === '1y') values = recent.slice(-(item.key === 'nfci' ? 52 : 260));
    else if (span === '5y') values = item.key === 'nfci' ? recent.slice(-260) : long;
    else values = long;
    const first = values[0]?.date || '—';
    const last = values.at(-1)?.date || '—';
    const label = span === 'max' ? `全部历史 · ${first} 至 ${last}` : `${span === '5y' ? '近5年' : '近1年'} · ${first} 至 ${last}`;
    $(`#liquidity-range-label-${item.key}`).textContent = label;
    drawLineChart($(`#liquidity-chart-${item.key}`), values, {
      height: 150,
      label: `${item.name}历史曲线`,
      color: colors[item.key],
      digits: item.key === 'nfci' ? 2 : 2,
      thresholds: [
        { value: item.riskLine, label: `风险线 ${fmt(item.riskLine, item.key === 'nfci' ? 2 : 1)}`, color: '#fb7185' },
        { value: item.opportunityLine, label: `机会线 ${fmt(item.opportunityLine, item.key === 'nfci' ? 2 : 1)}`, color: '#4ade80' },
      ],
    });
  };
  liquidity.indicators.forEach((item) => {
    const defaultSpan = item.key === 'nfci' ? 'max' : '5y';
    drawLiquidityChart(item, defaultSpan);
    document.querySelectorAll(`.liquidity-range-buttons[data-key="${item.key}"] button`).forEach((button) => {
      button.addEventListener('click', () => {
        button.parentElement.querySelectorAll('button').forEach((candidate) => candidate.classList.toggle('active', candidate === button));
        drawLiquidityChart(item, button.dataset.span);
      });
    });
  });
}

function renderMarketPulse(pulse, series) {
  if (!pulse) return;
  $('#market-pulse-summary').textContent = pulse.summary;
  const margin = pulse.margin || {};
  const marginValues = series.marginBalance?.values || [];
  $('#margin-card').innerHTML = `
    <div class="capital-card-head">
      <div><span class="capital-status ${margin.tone || 'observe'}">${margin.state || '等待数据'}</span><h3>沪深融资余额</h3></div>
      <div class="capital-number"><strong>${fmt(margin.value || 0, 3)}</strong><span>万亿元</span></div>
    </div>
    <div class="capital-facts">
      <span>融资买入额 <b>${fmt(margin.buy || 0, 1)}亿元</b></span>
      <span>近5期 <b class="${Number(margin.change5) > 0 ? 'up' : 'down'}">${Number(margin.change5) >= 0 ? '+' : ''}${fmt(margin.change5 || 0, 1)}%</b></span>
      <span>数据日 <b>${margin.date || '—'}</b></span>
    </div>
    <div class="capital-chart" id="margin-chart"></div>
    <p>${margin.interpretation || ''}</p>
    <div class="source-links"><a href="${series.marginBalance?.sourceUrl || '#'}" target="_blank" rel="noopener">上交所</a><a href="${series.marginBalance?.secondarySourceUrl || '#'}" target="_blank" rel="noopener">深交所</a></div>`;
  drawLineChart($('#margin-chart'), marginValues, { height: 155, label: '沪深融资余额', color: '#5eead4', digits: 3 });

  const etf = pulse.etfFlow || {};
  $('#etf-flow-card').innerHTML = `
    <div class="capital-card-head">
      <div><span class="capital-status ${etf.tone || 'observe'}">${etf.state || '等待数据'}</span><h3>ETF资金净申购</h3></div>
      <div class="capital-number negative"><strong>${Number(etf.stock) >= 0 ? '+' : ''}${fmt(etf.stock || 0, 1)}</strong><span>${etf.unit || '亿元'} · 股票ETF</span></div>
    </div>
    <div class="capital-facts">
      <span>全市场ETF <b>${Number(etf.all) >= 0 ? '+' : ''}${fmt(etf.all || 0, 1)}${etf.unit || '亿元'}</b></span>
      <span>数据日 <b>${etf.date || '—'}</b></span>
      <span>口径 <b>${etf.source || '公开估算'}</b></span>
    </div>
    <div class="flow-columns">
      <div><span>净流入靠前</span>${(etf.inflows || []).map((item) => `<p class="inflow">${item}</p>`).join('')}</div>
      <div><span>净流出靠前</span>${(etf.outflows || []).map((item) => `<p class="outflow">${item}</p>`).join('')}</div>
    </div>
    <p>${etf.interpretation || ''}</p>
    <div class="source-links"><a href="${etf.sourceUrl || '#'}" target="_blank" rel="noopener">查看资金统计来源</a></div>`;
}

function renderCourseSectors(sectors) {
  const target = $('#course-sector-grid');
  if (!sectors?.length) {
    target.innerHTML = '<p class="chart-note">行业代理数据暂不可用。</p>';
    return;
  }
  const draw = (filter = 'all') => {
    const visible = filter === 'all' ? sectors : sectors.filter((item) => item.tone === filter);
    target.innerHTML = visible.length ? visible.map((item) => `
      <article class="sector-card ${item.tone}">
        <div class="sector-head">
          <div><span class="sector-role">${item.role}</span><h3>${item.name}</h3></div>
          <span class="sector-state">${item.state}</span>
        </div>
        <p class="sector-thesis">${item.thesis}</p>
        <div class="sector-market">
          <span>${item.proxy}</span><strong>${fmt(item.value, 3)}</strong>
          <span>5日 <b class="${Number(item.change5) >= 0 ? 'up' : 'down'}">${Number(item.change5) >= 0 ? '+' : ''}${fmt(item.change5, 1)}%</b></span>
          <span>20日 <b class="${Number(item.change20) >= 0 ? 'up' : 'down'}">${Number(item.change20) >= 0 ? '+' : ''}${fmt(item.change20, 1)}%</b></span>
        </div>
        <p class="sector-reading">${item.reading}</p>
        <details><summary>查看验证条件</summary><p>${item.validation}</p><small>${item.source} · ${item.date}</small></details>
      </article>`).join('') : '<p class="chart-note">当前没有落入这个状态的课程重点行业。</p>';
  };
  draw();
  document.querySelectorAll('#sector-filters button').forEach((button) => {
    button.addEventListener('click', () => {
      button.parentElement.querySelectorAll('button').forEach((candidate) => candidate.classList.toggle('active', candidate === button));
      draw(button.dataset.filter);
    });
  });
}

function renderCredit(credit) {
  if (!credit) return;
  $('#credit-period').textContent = `${credit.period || '—'} · ${credit.source || '中国人民银行'}`;
  const cards = [
    ['社融存量', `${fmt(credit.socialFinanceStock, 1)}万亿元`, `同比 ${credit.socialFinanceYoy >= 0 ? '+' : ''}${fmt(credit.socialFinanceYoy, 1)}%`, '总量流动性'],
    ['M2 / M1', `${fmt(credit.m2Yoy, 1)}% / ${fmt(credit.m1Yoy, 1)}%`, `剪刀差 ${fmt(credit.m2Yoy - credit.m1Yoy, 1)}个百分点`, '钱活不活'],
    ['人民币贷款', `${fmt(credit.rmbLoansYtd, 2)}万亿元`, '年初至今新增', '信用扩张'],
    ['居民贷款', `${credit.householdLoansYtd >= 0 ? '+' : ''}${fmt(credit.householdLoansYtd, 2)}万亿元`, '年初至今', '居民需求'],
    ['企业贷款', `${credit.corporateLoansYtd >= 0 ? '+' : ''}${fmt(credit.corporateLoansYtd, 2)}万亿元`, '年初至今', '企业融资'],
    ['银行间利率', `${fmt(credit.interbankRate, 2)}%`, `质押回购 ${fmt(credit.repoRate, 2)}%`, '短端资金'],
  ];
  $('#credit-grid').innerHTML = cards.map(([label, value, context, tag]) => `
    <article class="credit-card"><span>${label}</span><strong>${value}</strong><small>${context}</small><b>${tag}</b></article>`).join('');
  const gap = Number(credit.m2Yoy) - Number(credit.m1Yoy);
  const householdWeak = Number(credit.householdLoansYtd) < 0;
  $('#credit-reading').innerHTML = `
    <strong>${householdWeak ? '总量不差，居民信用仍弱。' : '信用正在改善，但还要看结构。'}</strong>
    <p>社融存量同比 ${fmt(credit.socialFinanceYoy, 1)}%，说明金融总量没有失速；M2比M1高 ${fmt(gap, 1)} 个百分点，${gap > 2 ? '资金活化程度仍不够' : '资金活化有所改善'}。${householdWeak ? `居民贷款年内减少 ${fmt(Math.abs(credit.householdLoansYtd), 2)} 万亿元，而企业贷款增加 ${fmt(credit.corporateLoansYtd, 2)} 万亿元，当前更像“企业和政府部门托底、居民需求偏弱”。` : '居民与企业信贷都在增加，需继续观察是否转化为消费、投资与盈利。'}</p>
    <a href="${credit.sourceUrl || '#'}" target="_blank" rel="noopener">查看人民银行原始报告</a>`;
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
    $(`#value-${key}`).innerHTML = `${fmt(last, key === 'turnover' ? 2 : 2)} <small>${item.unit}</small><span class="mini-change">20日 ${delta >= 0 ? '+' : ''}${fmt(delta, 1)}%</span><span class="mini-date">数据 ${values.at(-1).date}</span>`;
    drawLineChart($(`#chart-${key}`), values, { height: 150, label: `${item.label}历史曲线`, color, digits: key === 'turnover' ? 1 : 2 });
  });
}

function renderChina(china, report) {
  if (report) {
    $('#china-analysis').innerHTML = `
      <article class="china-report ${report.tone || 'neutral'}">
        <div class="china-report-head">
          <div><span class="china-stance">${report.stance}</span><h3>${report.title}</h3></div>
          <span class="china-period">${report.period} · ${report.releaseDate || '官方数据'}</span>
        </div>
        <p class="china-summary">${report.summary}</p>
        <div class="china-contradiction"><span>核心矛盾</span><strong>${report.contradiction}</strong><small>${report.trendSummary}</small></div>
        <div class="china-signal-grid">
          ${report.signals.map((item) => `
            <section class="china-signal ${item.tone}">
              <div><span>${item.label}</span><b>${item.status}</b></div>
              <strong>${item.data}</strong>
              <p>${item.analysis}</p>
            </section>`).join('')}
        </div>
        <h4>对家庭资产判断意味着什么</h4>
        <div class="china-asset-grid">
          ${report.assetImplications.map((item) => `
            <section class="china-asset ${item.tone}"><span>${item.label}</span><strong>${item.title}</strong><p>${item.text}</p></section>`).join('')}
        </div>
        <div class="china-watch">
          <strong>下一步只看三个确认点</strong>
          ${report.watchPoints.map((item) => `<div class="${item.met ? 'met' : ''}"><i>${item.met ? '✓' : '○'}</i><span>${item.label}</span><p>${item.condition}</p></div>`).join('')}
        </div>
      </article>`;
  }
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
    const cacheKey = Date.now();
    const [response, eventResponse] = await Promise.all([
      fetch(`data.json?v=${cacheKey}`, { cache: 'no-store' }),
      fetch(`events.json?v=${cacheKey}`, { cache: 'no-store' }).catch(() => null),
    ]);
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const data = await response.json();
    const eventData = eventResponse?.ok ? await eventResponse.json() : null;
    const updated = new Date(data.meta.updatedAt);
    const updatedLabel = updated.toLocaleString('zh-CN', {
      month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false,
    });
    $('#freshness').textContent = `数据更新 ${updatedLabel}`;
    const warnings = data.meta.warnings || [];
    if (data.meta.status !== 'ok' || warnings.length) $('#freshness').textContent += ' · 部分行情沿用前值';
    $('#freshness').title = [...(data.meta.errors || []), ...warnings].join('\n');
    renderGauge(data.temperature);
    renderSectorMap(data.courseSectors);
    renderDailyReport(data.dailyReport);
    renderTemperatureChart(data.temperature, data.timelineEvents || []);
    renderGlobalEventAxis(eventData?.events || data.globalEvents || [], eventData?.updatedAt || data.meta.updatedAt);
    renderComponents(data.temperature.components);
    renderLiquidity(data.liquidity, data.series);
    renderMarketPulse(data.marketPulse, data.series);
    renderCourseSectors(data.courseSectors);
    renderMiniCharts(data.series);
    renderCredit(data.credit);
    renderChina(data.china, data.chinaReport);
  } catch (error) {
    $('#freshness').textContent = '数据读取失败，请稍后刷新';
    $('#plain-answer').textContent = '页面框架正常，但本次没有读到数据文件。';
    console.error(error);
  }
}

$('#event-dialog-close').addEventListener('click', () => $('#event-dialog').close());
$('#event-dialog').addEventListener('click', (event) => {
  if (event.target === $('#event-dialog')) $('#event-dialog').close();
});
$('#sector-dialog-close').addEventListener('click', () => $('#sector-dialog').close());
$('#sector-dialog').addEventListener('click', (event) => {
  if (event.target === $('#sector-dialog')) $('#sector-dialog').close();
});
$('#market-event-dialog-close').addEventListener('click', () => $('#market-event-dialog').close());
$('#market-event-dialog').addEventListener('click', (event) => {
  if (event.target === $('#market-event-dialog')) $('#market-event-dialog').close();
});

start();
