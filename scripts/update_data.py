#!/usr/bin/env python3
"""Refresh the public-data dashboard without API keys.

Sources:
- 国家统计局 release pages for monthly China macro data.
- 东方财富 public market quote endpoints for daily market curves.

Failures are non-destructive: the last successful value is preserved and the
dashboard records which source failed during this run.
"""

from __future__ import annotations

import bisect
import datetime as dt
import html
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
    command = ["curl", "-L", "-sS", "--max-time", str(timeout), "-A", UA]
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


def update_market(data: dict) -> None:
    specs = {
        "us10y": ("171.US10Y", "美国10年期国债收益率", "%", "收益率越高，成长估值压力通常越大"),
        "dollar": ("100.UDI", "美元指数", "", "美元走强通常意味着全球流动性偏紧"),
        "oil": ("102.CL00Y", "NYMEX原油", "美元/桶", "油价快速上涨会增加通胀与利率压力"),
        "gold": ("101.GC00Y", "COMEX黄金", "美元/盎司", "用于观察避险需求与实际利率预期"),
        "copper": ("101.HG00Y", "COMEX铜", "美元/磅", "用于观察全球制造业需求与周期热度"),
    }
    series = data.setdefault("series", {})
    for key, (secid, label, unit, meaning) in specs.items():
        rows = eastmoney_kline(secid)
        series[key] = {
            "label": label,
            "unit": unit,
            "meaning": meaning,
            "source": "东方财富公开行情",
            "values": [{"date": row["date"], "value": row["value"]} for row in rows],
        }
        time.sleep(0.7)

    sh = eastmoney_kline("1.000001")
    time.sleep(0.7)
    sz = eastmoney_kline("0.399001")
    sh_amount = {row["date"]: row["amount"] for row in sh}
    sz_amount = {row["date"]: row["amount"] for row in sz}
    dates = sorted(set(sh_amount) & set(sz_amount))
    values = [{"date": day, "value": round((sh_amount[day] + sz_amount[day]) / 1e12, 3)} for day in dates]
    series["turnover"] = {
        "label": "A股成交额",
        "unit": "万亿元",
        "meaning": "2万亿元是活跃分界，2.2–2.3万亿元以上更利于行情扩散",
        "source": "上证指数与深证成指成交额合计（东方财富公开行情）",
        "values": values,
    }


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


def build_temperature(data: dict) -> None:
    series = data["series"]
    china = data["china"]
    us10y = latest(series, "us10y", 5.0)
    dollar_values = series.get("dollar", {}).get("values", [])
    oil_values = series.get("oil", {}).get("values", [])
    turnover_values = series.get("turnover", {}).get("values", [])

    rate = clamp((6.0 - us10y) * 25)
    dollar = clamp(50 - pct_change(dollar_values, 20) * 4)
    growth = growth_score(china)
    market = market_score(latest(series, "turnover", 1.8))
    inflation = clamp(55 - pct_change(oil_values, 20) * 2)
    score = rate * 0.25 + dollar * 0.15 + growth * 0.25 + market * 0.25 + inflation * 0.10

    label, action, explanation = temperature_label(score)
    components = [
        {"name": "利率压力", "score": round1(rate), "weight": 25, "reading": f"美国10年期 {us10y:.2f}%"},
        {"name": "美元流动性", "score": round1(dollar), "weight": 15, "reading": f"20日变化 {pct_change(dollar_values, 20):+.1f}%"},
        {"name": "国内增长", "score": round1(growth), "weight": 25, "reading": f"工业 {china['industrial']:+.1f}% / 消费 {china['retail']:+.1f}%"},
        {"name": "市场资金", "score": round1(market), "weight": 25, "reading": f"成交 {latest(series, 'turnover', 0):.2f}万亿元"},
        {"name": "通胀压力", "score": round1(inflation), "weight": 10, "reading": f"油价20日 {pct_change(oil_values, 20):+.1f}%"},
    ]

    history = []
    dates = [item["date"] for item in turnover_values]
    for day in dates[-180:]:
        y = value_on_or_before(series["us10y"]["values"], day, us10y)
        d_values = [item for item in dollar_values if item["date"] <= day]
        o_values = [item for item in oil_values if item["date"] <= day]
        t = value_on_or_before(turnover_values, day, latest(series, "turnover", 1.8))
        r_score = clamp((6.0 - y) * 25)
        d_score = clamp(50 - pct_change(d_values, 20) * 4)
        i_score = clamp(55 - pct_change(o_values, 20) * 2)
        day_score = r_score * 0.25 + d_score * 0.15 + growth * 0.25 + market_score(t) * 0.25 + i_score * 0.10
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
    if DATA_PATH.exists():
        try:
            existing = json.loads(DATA_PATH.read_text(encoding="utf-8"))
            data.update(existing)
        except Exception:
            pass

    errors = []
    try:
        update_nbs(data)
    except Exception as exc:
        errors.append(f"国家统计局更新失败：{exc}")
    try:
        update_market(data)
    except Exception as exc:
        errors.append(f"行情更新失败：{exc}")

    if data.get("series"):
        build_temperature(data)
    now = dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).replace(microsecond=0)
    data["meta"] = {
        "updatedAt": now.isoformat(),
        "status": "ok" if not errors else "partial",
        "errors": errors,
        "note": "自动更新公开数据；付费课程原文与附件未发布。",
    }
    DATA_PATH.parent.mkdir(parents=True, exist_ok=True)
    DATA_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"updatedAt": data["meta"]["updatedAt"], "status": data["meta"]["status"], "errors": errors}, ensure_ascii=False))


if __name__ == "__main__":
    main()
