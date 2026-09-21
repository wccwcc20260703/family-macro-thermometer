#!/usr/bin/env python3
"""Refresh the public-data dashboard without API keys.

Sources:
- 国家统计局 release pages for monthly China macro data.
- 东方财富 public market quote endpoints for daily market curves.

Failures are non-destructive: the last successful value is preserved and the
dashboard records which source failed during this run.
"""

from __future__ import annotations

import base64
import bisect
import csv
import datetime as dt
import gzip
import html
import io
import json
import math
import re
import subprocess
import time
import urllib.parse
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DATA_PATH = ROOT / "dist" / "data.json"
SEED_PATH = ROOT / "scripts" / "seed_data.json.gz.b64"
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"


def fetch_text(url: str, timeout: int = 25, headers: dict | None = None) -> str:
    last_error = None
    for attempt in range(1):
        try:
            request_headers = {"User-Agent": UA, "Accept": "*/*", "Connection": "close"}
            request_headers.update(headers or {})
            req = urllib.request.Request(url, headers=request_headers)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read().decode("utf-8", errors="replace")
        except Exception as exc:
            last_error = exc
    command = ["curl", "--http1.1", "-L", "-sS", "--max-time", str(timeout), "-A", UA]
    for key, value in (headers or {}).items():
        command.extend(["-H", f"{key}: {value}"])
    command.append(url)
    try:
        return subprocess.check_output(command, text=True, timeout=timeout + 5)
    except Exception:
        raise last_error


def clamp(value: float, low: float = 0, high: float = 100) -> float:
    return max(low, min(high, value))


def round1(value: float) -> float:
    return round(float(value), 1)


def fallback_data() -> dict:
    return {
        "meta": {
            "updatedAt": "2026-09-21T18:00:00+08:00",
            "status": "seed",
            "errors": [],
            "note": "公开数据看板；付费课程原文与附件未发布。",
        },
        "china": {
            "period": "2026-08",
            "releaseDate": "2026-09-15",
            "sourceUrl": "https://www.stats.gov.cn/sj/zxfb/202609/t20260915_1965307.html",
            "industrial": 5.3,
            "retail": 1.1,
            "fixedAsset": -7.2,
            "realEstate": -19.9,
            "highTechMonthly": 16.7,
            "industrialProfit": 17.6,
            "profitPeriod": "2026-07",
            "cpi": 0.8,
        },
        "chinaHistory": [
            {"period": "2026-07", "industrial": 5.3, "retail": 1.2, "fixedAsset": -6.7, "realEstate": -19.2, "industrialProfit": 17.6},
            {"period": "2026-08", "industrial": 5.3, "retail": 1.1, "fixedAsset": -7.2, "realEstate": -19.9, "industrialProfit": 17.6},
        ],
        "series": {},
        "temperature": {},
    }


def strip_html(raw: str) -> str:
    raw = re.sub(r"<script[\s\S]*?</script>", " ", raw, flags=re.I)
    raw = re.sub(r"<style[\s\S]*?</style>", " ", raw, flags=re.I)
    raw = re.sub(r"<[^>]+>", " ", raw)
    return re.sub(r"\s+", "", html.unescape(raw))


def signed_value(text: str, pattern: str, default: float) -> float:
    match = re.search(pattern, text)
    if not match:
        return default
    direction, number = match.groups()
    value = float(number)
    return -value if direction in {"下降", "减少"} else value


def update_nbs(data: dict) -> None:
    listing_url = "https://www.stats.gov.cn/sj/zxfb/"
    listing = fetch_text(listing_url)
    releases = re.findall(r'href="([^"]+)"[^>]+title=[\'\"]([^\'\"]+)[\'\"]', listing)

    summary = next(((href, title) for href, title in releases if re.search(r"\d+月份国民经济运行", title)), None)
    if not summary:
        raise RuntimeError("未找到国家统计局月度经济运行页面")

    href, title = summary
    summary_url = urllib.parse.urljoin(listing_url, href)
    page = fetch_text(summary_url)
    text = strip_html(page)

    release_match = re.search(r"(20\d{2})年(\d+)月(\d+)日", text)
    data_month_match = re.search(r"(\d+)月份国民经济运行", title)
    year = int(release_match.group(1)) if release_match else dt.datetime.now().year
    month = int(data_month_match.group(1)) if data_month_match else max(1, dt.datetime.now().month - 1)
    current = data["china"]

    current["period"] = f"{year:04d}-{month:02d}"
    current["releaseDate"] = release_match.group(0) if release_match else current.get("releaseDate")
    current["sourceUrl"] = summary_url
    current["industrial"] = signed_value(text, rf"1[—－-]{month}月份，?规模以上工业增加值同比(增长|下降)([\d.]+)%", current["industrial"])
    current["retail"] = signed_value(text, rf"1[—－-]{month}月份，?社会消费品零售总额[^。；]*?同比(增长|下降)([\d.]+)%", current["retail"])
    current["fixedAsset"] = signed_value(text, rf"1[—－-]{month}月份，?全国固定资产投资（不含农户）[^。；]*?同比(增长|下降)([\d.]+)%", current["fixedAsset"])
    current["realEstate"] = signed_value(text, rf"1[—－-]{month}月份，?全国房地产开发投资[^。；]*?同比(增长|下降)([\d.]+)%", current["realEstate"])
    current["highTechMonthly"] = signed_value(text, r"高技术制造业增加值(增长|下降)([\d.]+)%", current["highTechMonthly"])

    profit_release = next(((href, title) for href, title in releases if "规模以上工业企业利润" in title), None)
    if profit_release:
        p_href, p_title = profit_release
        p_match = re.search(r"(20\d{2})年1[—－-](\d+)月份全国规模以上工业企业利润(增长|下降)([\d.]+)%", p_title)
        if p_match:
            py, pm, direction, value = p_match.groups()
            current["industrialProfit"] = -float(value) if direction == "下降" else float(value)
            current["profitPeriod"] = f"{int(py):04d}-{int(pm):02d}"
            current["profitSourceUrl"] = urllib.parse.urljoin(listing_url, p_href)

    cpi_release = next((title for _, title in releases if "居民消费价格同比" in title), None)
    if cpi_release:
        cpi_match = re.search(r"居民消费价格同比(上涨|下降)([\d.]+)%", cpi_release)
        if cpi_match:
            current["cpi"] = -float(cpi_match.group(2)) if cpi_match.group(1) == "下降" else float(cpi_match.group(2))

    entry = {
        "period": current["period"],
        "industrial": current["industrial"],
        "retail": current["retail"],
        "fixedAsset": current["fixedAsset"],
        "realEstate": current["realEstate"],
        "industrialProfit": current["industrialProfit"],
    }
    history = [item for item in data.get("chinaHistory", []) if item.get("period", "") < entry["period"]]
    history.append(entry)
    data["chinaHistory"] = sorted(history, key=lambda item: item["period"])[-36:]


def eastmoney_kline(secid: str, limit: int = 260) -> list[dict]:
    params = urllib.parse.urlencode({
        "secid": secid,
        "klt": 101,
        "fqt": 1,
        "lmt": limit,
        "end": 20500000,
        "fields1": "f1,f2,f3,f4,f5,f6",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
    })
    raw = fetch_text(
        f"https://push2his.eastmoney.com/api/qt/stock/kline/get?{params}",
        headers={"Referer": "https://quote.eastmoney.com/"},
    )
    payload = json.loads(raw)
    lines = (payload.get("data") or {}).get("klines") or []
    result = []
    for line in lines:
        parts = line.split(",")
        if len(parts) >= 7 and parts[2] not in {"", "-"}:
            result.append({"date": parts[0], "value": float(parts[2]), "amount": float(parts[6])})
    if not result:
        raise RuntimeError(f"行情序列为空: {secid}")
    return result


def yahoo_kline(symbol: str, limit: int = 260) -> list[dict]:
    encoded = urllib.parse.quote(symbol, safe="")
    raw = fetch_text(
        f"https://query1.finance.yahoo.com/v8/finance/chart/{encoded}?range=1y&interval=1d",
        headers={"Referer": "https://finance.yahoo.com/"},
    )
    payload = json.loads(raw)
    result = payload["chart"]["result"][0]
    timestamps = result.get("timestamp") or []
    closes = ((result.get("indicators") or {}).get("quote") or [{}])[0].get("close") or []
    rows = []
    for stamp, close in zip(timestamps, closes):
        if close is None:
            continue
        day = dt.datetime.fromtimestamp(stamp, tz=dt.timezone.utc).date().isoformat()
        rows.append({"date": day, "value": float(close), "amount": 0.0})
    if not rows:
        raise RuntimeError(f"Yahoo行情序列为空: {symbol}")
    return rows[-limit:]


def tencent_current_amount(symbol: str) -> tuple[str, float]:
    params = urllib.parse.urlencode({"param": f"{symbol},day,,,5,qfq"})
    raw = fetch_text(
        f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?{params}",
        headers={"Referer": "https://gu.qq.com/"},
    )
    payload = json.loads(raw)
    quote = payload["data"][symbol]["qt"][symbol]
    day = quote[30][:8]
    day = f"{day[:4]}-{day[4:6]}-{day[6:8]}"
    amount = float(quote[35].split("/")[2])
    return day, amount


def fred_series(series_id: str, limit: int = 520) -> list[dict]:
    url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}"
    raw = subprocess.check_output(
        ["curl", "--http1.1", "-L", "-sS", "--connect-timeout", "5", "--max-time", "20", url],
        text=True,
        timeout=25,
    )
    rows = []
    for row in csv.DictReader(io.StringIO(raw)):
        value = row.get(series_id)
        if not value or value == ".":
            continue
        day = row.get("observation_date") or row.get("DATE")
        if not day:
            continue
        rows.append({"date": day, "value": float(value)})
    if not rows:
        raise RuntimeError(f"FRED序列为空: {series_id}")
    return rows[-limit:]


def update_fred_liquidity(data: dict) -> list[str]:
    specs = {
        "effr": ("EFFR", "EFFR 有效联邦基金利率", "%", "美元短端资金价格", "每日"),
        "real10y": ("DFII10", "美国10年实际利率", "%", "长期真实资金成本与成长股折现率", "每日"),
        "broadDollar": ("DTWEXBGS", "广义美元指数", "2006=100", "美元相对全球主要贸易伙伴货币的强弱", "每日"),
        "nfci": ("NFCI", "NFCI 金融状况指数", "指数", "美国货币、债券、股票和银行体系的综合松紧", "每周"),
    }
    series = data.setdefault("series", {})
    warnings = []
    for key, (series_id, label, unit, meaning, frequency) in specs.items():
        try:
            rows = fred_series(series_id)
        except Exception as exc:
            if not (series.get(key) or {}).get("values"):
                raise RuntimeError(f"{label}更新失败: {exc}") from exc
            warnings.append(f"{label}暂未取得新值，保留最近成功记录")
            continue
        series[key] = {
            "label": label,
            "unit": unit,
            "meaning": meaning,
            "frequency": frequency,
            "source": "FRED（圣路易斯联储）",
            "sourceUrl": f"https://fred.stlouisfed.org/series/{series_id}",
            "values": rows,
        }
        time.sleep(0.35)
    return warnings


def update_market(data: dict) -> list[str]:
    specs = {
        "us10y": ("171.US10Y", "^TNX", "美国10年期国债收益率", "%", "收益率越高，成长估值压力通常越大"),
        "dollar": ("100.UDI", "DX-Y.NYB", "美元指数", "", "美元走强通常意味着全球流动性偏紧"),
        "oil": ("102.CL00Y", "CL=F", "NYMEX原油", "美元/桶", "油价快速上涨会增加通胀与利率压力"),
        "gold": ("101.GC00Y", "GC=F", "COMEX黄金", "美元/盎司", "用于观察避险需求与实际利率预期"),
        "copper": ("101.HG00Y", "HG=F", "COMEX铜", "美元/磅", "用于观察全球制造业需求与周期热度"),
    }
    series = data.setdefault("series", {})
    warnings = []
    for key, (secid, yahoo_symbol, label, unit, meaning) in specs.items():
        source = "东方财富公开行情"
        try:
            rows = eastmoney_kline(secid)
        except Exception as primary_exc:
            try:
                rows = yahoo_kline(yahoo_symbol)
                source = "Yahoo Finance公开行情（备用源）"
            except Exception as backup_exc:
                existing = series.get(key) or {}
                if not existing.get("values"):
                    raise RuntimeError(f"{label}主备数据源均不可用: {primary_exc}; {backup_exc}") from backup_exc
                warnings.append(f"{label}暂未取得新值，保留最近成功记录")
                continue
        series[key] = {
            "label": label,
            "unit": unit,
            "meaning": meaning,
            "source": source,
            "values": [{"date": row["date"], "value": row["value"]} for row in rows],
        }
        time.sleep(0.7)

    turnover_source = "上证指数与深证成指成交额合计（东方财富公开行情）"
    try:
        sh = eastmoney_kline("1.000001")
        time.sleep(0.7)
        sz = eastmoney_kline("0.399001")
        sh_amount = {row["date"]: row["amount"] for row in sh}
        sz_amount = {row["date"]: row["amount"] for row in sz}
        dates = sorted(set(sh_amount) & set(sz_amount))
        values = [{"date": day, "value": round((sh_amount[day] + sz_amount[day]) / 1e12, 3)} for day in dates]
    except Exception as primary_exc:
        try:
            sh_day, sh_amount = tencent_current_amount("sh000001")
            sz_day, sz_amount = tencent_current_amount("sz399001")
            day = min(sh_day, sz_day)
            previous = (series.get("turnover") or {}).get("values") or []
            values = [item for item in previous if item.get("date") != day]
            values.append({"date": day, "value": round((sh_amount + sz_amount) / 1e12, 3)})
            values = sorted(values, key=lambda item: item["date"])[-260:]
            turnover_source = "腾讯行情当日成交额；历史数据沿用最近成功记录"
        except Exception as backup_exc:
            existing = series.get("turnover") or {}
            values = existing.get("values") or []
            if not values:
                raise RuntimeError(f"A股成交额主备数据源均不可用: {primary_exc}; {backup_exc}") from backup_exc
            turnover_source = existing.get("source") or "最近成功记录"
            warnings.append("A股成交额暂未取得新值，保留最近成功记录")
    series["turnover"] = {
        "label": "A股成交额",
        "unit": "万亿元",
        "meaning": "2万亿元是活跃分界，2.2–2.3万亿元以上更利于行情扩散",
        "source": turnover_source,
        "values": values,
    }
    return warnings


def latest(series: dict, key: str, default: float) -> float:
    values = (series.get(key) or {}).get("values") or []
    return float(values[-1]["value"]) if values else default


def pct_change(values: list[dict], sessions: int) -> float:
    if len(values) <= sessions:
        return 0.0
    start = float(values[-sessions - 1]["value"])
    end = float(values[-1]["value"])
    return 0.0 if start == 0 else (end / start - 1) * 100


def growth_score(china: dict) -> float:
    parts = [
        clamp((china.get("industrial", 0) + 2) / 10 * 100),
        clamp((china.get("retail", 0) + 3) / 11 * 100),
        clamp((china.get("fixedAsset", 0) + 15) / 25 * 100),
        clamp((china.get("realEstate", 0) + 30) / 30 * 100),
        clamp((china.get("industrialProfit", 0) + 20) / 50 * 100),
    ]
    return sum(parts) / len(parts)


def value_on_or_before(values: list[dict], day: str, default: float) -> float:
    dates = [item["date"] for item in values]
    idx = bisect.bisect_right(dates, day) - 1
    return float(values[idx]["value"]) if idx >= 0 else default


def market_score(turnover: float) -> float:
    points = [(0.6, 10), (1.0, 25), (1.5, 42), (2.0, 60), (2.5, 80), (3.2, 95)]
    if turnover <= points[0][0]:
        return points[0][1]
    for (x0, y0), (x1, y1) in zip(points, points[1:]):
        if turnover <= x1:
            return y0 + (turnover - x0) / (x1 - x0) * (y1 - y0)
    return 100


def temperature_label(score: float) -> tuple[str, str, str]:
    if score < 35:
        return "偏冷", "防守", "现金与低波资产优先，进攻仓只保留高确定性方向。"
    if score < 50:
        return "谨慎", "小步试仓", "可以观察和试仓，但每次加仓都要等数据或趋势确认。"
    if score < 65:
        return "均衡", "结构选择", "环境不差但不是全面进攻，重点选择盈利与资金同时改善的方向。"
    if score < 80:
        return "偏热", "积极但不追高", "风险偏好改善，可提高进攻仓，但要检查估值和拥挤度。"
    return "过热", "锁定收益", "市场很热不代表更安全，应降低追涨并准备分批兑现。"


def series_until(series: dict, key: str, day=None) -> list[dict]:
    values = (series.get(key) or {}).get("values") or []
    return values if day is None else [item for item in values if item["date"] <= day]


def dollar_score(values: list[dict]) -> float:
    if not values:
        return 50.0
    window = values[-260:]
    current = float(window[-1]["value"])
    low = min(float(item["value"]) for item in window)
    high = max(float(item["value"]) for item in window)
    position = 50.0 if high == low else (current - low) / (high - low) * 100
    trend = clamp(50 - pct_change(window, 20) * 5)
    return clamp((100 - position) * 0.7 + trend * 0.3)


def liquidity_scores(series: dict, day=None) -> dict:
    effr_values = series_until(series, "effr", day)
    real_values = series_until(series, "real10y", day)
    dollar_values = series_until(series, "broadDollar", day)
    nfci_values = series_until(series, "nfci", day)
    effr = float(effr_values[-1]["value"]) if effr_values else 4.0
    real10y = float(real_values[-1]["value"]) if real_values else 2.0
    nfci = float(nfci_values[-1]["value"]) if nfci_values else 0.0
    return {
        "effr": clamp((6.0 - effr) / 4.5 * 100),
        "real10y": clamp((3.0 - real10y) / 3.0 * 100),
        "broadDollar": dollar_score(dollar_values),
        "nfci": clamp(50 - nfci * 65),
    }


def build_liquidity(data: dict) -> None:
    series = data["series"]
    scores = liquidity_scores(series)
    composite = sum(scores.values()) / len(scores)

    def point(key: str, fallback: float = 0.0) -> tuple[float, str]:
        values = series_until(series, key)
        if not values:
            return fallback, "—"
        return float(values[-1]["value"]), values[-1]["date"]

    effr, effr_date = point("effr", 4.0)
    real10y, real_date = point("real10y", 2.0)
    broad, broad_date = point("broadDollar", 120.0)
    nfci, nfci_date = point("nfci", 0.0)
    broad_values = series_until(series, "broadDollar")
    broad_change = pct_change(broad_values, 20)

    if effr <= 2.5:
        effr_state = ("偏松", "green", "短端美元资金成本已进入较宽松区间。")
    elif effr <= 4.0:
        effr_state = ("中性偏紧", "amber", "较高点有所回落，但还不能视为全面宽松。")
    else:
        effr_state = ("偏紧", "red", "短端资金成本仍高，融资与估值都承压。")

    if real10y <= 1.2:
        real_state = ("友好", "green", "长期真实资金成本较低，对成长估值更友好。")
    elif real10y <= 2.0:
        real_state = ("中性偏高", "amber", "折现率仍有压力，需要盈利兑现来消化估值。")
    else:
        real_state = ("高位约束", "red", "长期真实利率偏高，高估值和长久期资产更敏感。")

    if broad_change > 2.0:
        dollar_state = ("明显走强", "red", "美元加速走强，可能收紧全球流动性。")
    elif broad_change > 0.5:
        dollar_state = ("温和走强", "amber", "美元有一定上行压力，但尚未形成失控抽水。")
    else:
        dollar_state = ("未明显抽水", "green", "美元没有快速走强，全球风险资产少一层压力。")

    if nfci < -0.25:
        nfci_state = ("金融条件宽松", "green", "金融市场整体仍比长期平均水平宽松。")
    elif nfci <= 0.25:
        nfci_state = ("接近中性", "amber", "金融条件没有明显放松，也未显著收紧。")
    else:
        nfci_state = ("金融条件收紧", "red", "金融体系整体压力上升，需要优先控制风险。")

    if composite < 35:
        label = "偏紧"
        summary = "美元流动性对风险资产形成明显约束，宜提高安全边际。"
    elif composite < 55:
        label = "紧中有松"
        summary = "短端与实际利率仍有压力，但美元和金融条件尚未出现系统性抽水。"
    elif composite < 75:
        label = "中性偏松"
        summary = "流动性环境总体友好，但仍需观察利率是否继续回落。"
    else:
        label = "宽松"
        summary = "美元资金与金融条件共同转松，风险偏好通常更容易扩散。"

    data["liquidity"] = {
        "score": round1(composite),
        "label": label,
        "summary": summary,
        "indicators": [
            {
                "key": "effr", "name": "EFFR 有效联邦基金利率", "value": effr, "unit": "%", "date": effr_date,
                "score": round1(scores["effr"]), "status": effr_state[0], "tone": effr_state[1], "meaning": "美元短端资金价格", "interpretation": effr_state[2],
                "opportunity": "继续下行意味着现金与融资成本缓解，风险资产估值可获得支撑。",
                "risk": "若维持高位或重新上行，说明政策利率层面的宽松仍不充分。",
            },
            {
                "key": "real10y", "name": "美国10年实际利率", "value": real10y, "unit": "%", "date": real_date,
                "score": round1(scores["real10y"]), "status": real_state[0], "tone": real_state[1], "meaning": "长期真实资金成本 / 成长股折现率", "interpretation": real_state[2],
                "opportunity": "持续回落通常利好黄金、成长股及其他长久期资产。",
                "risk": "高位或再创新高会压缩高估值资产的容错空间。",
            },
            {
                "key": "broadDollar", "name": "广义美元指数", "value": broad, "unit": "", "date": broad_date,
                "score": round1(scores["broadDollar"]), "status": dollar_state[0], "tone": dollar_state[1], "meaning": "美元相对全球货币的强弱 / 全球美元压力", "interpretation": dollar_state[2],
                "opportunity": "美元走弱或平稳时，新兴市场、大宗商品与非美风险资产压力减轻。",
                "risk": "20日快速升值超过约2%时，需要警惕全球流动性收缩。",
            },
            {
                "key": "nfci", "name": "NFCI 金融状况指数", "value": nfci, "unit": "", "date": nfci_date,
                "score": round1(scores["nfci"]), "status": nfci_state[0], "tone": nfci_state[1], "meaning": "美国金融体系综合松紧", "interpretation": nfci_state[2],
                "opportunity": "负值延续意味着市场融资与风险承受力仍有缓冲。",
                "risk": "一旦快速向0或正值上行，往往比单看政策利率更早暴露压力。",
            },
        ],
    }


def build_temperature(data: dict) -> None:
    series = data["series"]
    china = data["china"]
    liquidity = liquidity_scores(series)
    oil_values = series.get("oil", {}).get("values", [])
    turnover_values = series.get("turnover", {}).get("values", [])

    growth = growth_score(china)
    market = market_score(latest(series, "turnover", 1.8))
    inflation = clamp(55 - pct_change(oil_values, 20) * 2)
    score = (
        liquidity["effr"] * 0.10 + liquidity["real10y"] * 0.10 +
        liquidity["broadDollar"] * 0.10 + liquidity["nfci"] * 0.10 +
        growth * 0.25 + market * 0.25 + inflation * 0.10
    )

    label, action, explanation = temperature_label(score)
    components = [
        {"name": "短端利率", "score": round1(liquidity["effr"]), "weight": 10, "reading": f"EFFR {latest(series, 'effr', 4.0):.2f}%"},
        {"name": "真实利率", "score": round1(liquidity["real10y"]), "weight": 10, "reading": f"10年实际利率 {latest(series, 'real10y', 2.0):.2f}%"},
        {"name": "美元强弱", "score": round1(liquidity["broadDollar"]), "weight": 10, "reading": f"广义美元 {latest(series, 'broadDollar', 120):.2f}"},
        {"name": "金融松紧", "score": round1(liquidity["nfci"]), "weight": 10, "reading": f"NFCI {latest(series, 'nfci', 0):+.3f}"},
        {"name": "国内增长", "score": round1(growth), "weight": 25, "reading": f"工业 {china['industrial']:+.1f}% / 消费 {china['retail']:+.1f}%"},
        {"name": "市场资金", "score": round1(market), "weight": 25, "reading": f"成交 {latest(series, 'turnover', 0):.2f}万亿元"},
        {"name": "通胀压力", "score": round1(inflation), "weight": 10, "reading": f"油价20日 {pct_change(oil_values, 20):+.1f}%"},
    ]

    history = []
    dates = [item["date"] for item in turnover_values]
    for day in dates[-180:]:
        day_liquidity = liquidity_scores(series, day)
        o_values = [item for item in oil_values if item["date"] <= day]
        t = value_on_or_before(turnover_values, day, latest(series, "turnover", 1.8))
        i_score = clamp(55 - pct_change(o_values, 20) * 2)
        day_score = (
            day_liquidity["effr"] * 0.10 + day_liquidity["real10y"] * 0.10 +
            day_liquidity["broadDollar"] * 0.10 + day_liquidity["nfci"] * 0.10 +
            growth * 0.25 + market_score(t) * 0.25 + i_score * 0.10
        )
        history.append({"date": day, "value": round1(day_score)})

    values = [item["value"] for item in history]
    avg30 = sum(values[-30:]) / max(1, len(values[-30:]))
    avg90 = sum(values[-90:]) / max(1, len(values[-90:]))
    strongest = max(components, key=lambda item: item["score"])
    weakest = min(components, key=lambda item: item["score"])
    data["temperature"] = {
        "score": round1(score),
        "label": label,
        "action": action,
        "explanation": explanation,
        "avg30": round1(avg30),
        "avg90": round1(avg90),
        "rangeLow": round1(min(values) if values else score),
        "rangeHigh": round1(max(values) if values else score),
        "strongest": strongest["name"],
        "weakest": weakest["name"],
        "components": components,
        "history": history,
        "bands": [
            {"from": 0, "to": 35, "label": "偏冷·防守"},
            {"from": 35, "to": 50, "label": "谨慎·试仓"},
            {"from": 50, "to": 65, "label": "均衡·精选"},
            {"from": 65, "to": 80, "label": "偏热·积极"},
            {"from": 80, "to": 100, "label": "过热·降温"},
        ],
    }


def main() -> None:
    data = fallback_data()
    seed = None
    if SEED_PATH.exists():
        try:
            packed = base64.b64decode(SEED_PATH.read_text(encoding="ascii"))
            seed = json.loads(gzip.decompress(packed).decode("utf-8"))
        except Exception:
            seed = None
    if DATA_PATH.exists():
        try:
            existing = json.loads(DATA_PATH.read_text(encoding="utf-8"))
            if existing.get("series"):
                data.update(existing)
            elif seed:
                data = seed
                data["china"].update(existing.get("china") or {})
                data["chinaHistory"] = existing.get("chinaHistory") or data.get("chinaHistory", [])
        except Exception:
            pass
    elif seed:
        data = seed

    errors = []
    warnings = []
    try:
        update_nbs(data)
    except Exception as exc:
        errors.append(f"国家统计局更新失败：{exc}")
    try:
        warnings.extend(update_fred_liquidity(data))
    except Exception as exc:
        errors.append(f"FRED流动性更新失败：{exc}")
    try:
        warnings.extend(update_market(data))
    except Exception as exc:
        errors.append(f"行情更新失败：{exc}")

    if data.get("series"):
        build_liquidity(data)
        build_temperature(data)
    now = dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).replace(microsecond=0)
    data["meta"] = {
        "updatedAt": now.isoformat(),
        "status": "ok" if not errors else "partial",
        "errors": errors,
        "warnings": warnings,
        "note": "自动更新公开数据；付费课程原文与附件未发布。",
    }
    DATA_PATH.parent.mkdir(parents=True, exist_ok=True)
    DATA_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"updatedAt": data["meta"]["updatedAt"], "status": data["meta"]["status"], "errors": errors, "warnings": warnings}, ensure_ascii=False))


if __name__ == "__main__":
    main()
