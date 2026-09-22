#!/usr/bin/env python3
"""Refresh the public-data dashboard without API keys.

Sources:
- 国家统计局 release pages for monthly China macro data.
- 中国人民银行 financial-statistics releases for domestic credit conditions.
- 上海、深圳证券交易所 daily margin-financing disclosures.
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
import zipfile
import xml.etree.ElementTree as ET
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


def fetch_bytes(url: str, timeout: int = 25, headers: dict | None = None) -> bytes:
    request_headers = {"User-Agent": UA, "Accept": "*/*", "Connection": "close"}
    request_headers.update(headers or {})
    try:
        req = urllib.request.Request(url, headers=request_headers)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()
    except Exception as first_error:
        command = ["curl", "--http1.1", "-L", "-sS", "--max-time", str(timeout), "-A", UA]
        for key, value in (headers or {}).items():
            command.extend(["-H", f"{key}: {value}"])
        command.append(url)
        try:
            return subprocess.check_output(command, timeout=timeout + 5)
        except Exception:
            raise first_error


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
            "industrialMonthly": 5.2,
            "industrialMonthlyChange": 0.7,
            "retail": 1.1,
            "retailMonthly": 0.4,
            "fixedAsset": -7.2,
            "fixedAssetExRealEstate": -4.2,
            "realEstate": -19.9,
            "realEstateSales": -13.0,
            "highTechMonthly": 16.7,
            "industrialProfit": 17.6,
            "profitPeriod": "2026-07",
            "cpi": 0.8,
            "coreCpi": 1.0,
            "ppi": 3.8,
            "manufacturingPmi": 49.8,
            "unemployment": 5.3,
            "exportsMonthly": 18.6,
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
    current["industrialMonthly"] = signed_value(text, rf"(?:^|。){month}月份，?全国规模以上工业增加值同比(增长|下降)([\d.]+)%", current.get("industrialMonthly", current["industrial"]))
    acceleration = re.search(rf"(?:^|。){month}月份，?全国规模以上工业增加值同比[^。；]*?比上月(加快|回落)([\d.]+)个百分点", text)
    if acceleration:
        current["industrialMonthlyChange"] = float(acceleration.group(2)) * (1 if acceleration.group(1) == "加快" else -1)
    current["retail"] = signed_value(text, rf"1[—－-]{month}月份，?社会消费品零售总额[^。；]*?同比(增长|下降)([\d.]+)%", current["retail"])
    current["retailMonthly"] = signed_value(text, rf"(?:^|。){month}月份，?社会消费品零售总额\d+亿元，?同比(增长|下降)([\d.]+)%", current.get("retailMonthly", current["retail"]))
    current["fixedAsset"] = signed_value(text, rf"1[—－-]{month}月份，?全国固定资产投资（不含农户）[^。；]*?同比(增长|下降)([\d.]+)%", current["fixedAsset"])
    current["fixedAssetExRealEstate"] = signed_value(text, r"扣除房地产开发的固定资产投资(增长|下降)([\d.]+)%", current.get("fixedAssetExRealEstate", current["fixedAsset"]))
    current["realEstate"] = signed_value(text, rf"1[—－-]{month}月份，?全国房地产开发投资[^。；]*?同比(增长|下降)([\d.]+)%", current["realEstate"])
    current["realEstateSales"] = signed_value(text, r"新建商品房销售额\d+亿元，?(增长|下降)([\d.]+)%", current.get("realEstateSales", current["realEstate"]))
    current["highTechMonthly"] = signed_value(text, r"高技术制造业增加值(增长|下降)([\d.]+)%", current["highTechMonthly"])
    current["coreCpi"] = signed_value(text, r"核心CPI同比(上涨|下降)([\d.]+)%", current.get("coreCpi", current.get("cpi", 0.0)))
    current["ppi"] = signed_value(text, rf"{month}月份，?全国工业生产者出厂价格同比(上涨|下降)([\d.]+)%", current.get("ppi", 0.0))
    current["exportsMonthly"] = signed_value(text, r"其中，?出口\d+亿元，?(增长|下降)([\d.]+)%", current.get("exportsMonthly", 0.0))

    pmi_match = re.search(rf"{month}月份，?制造业采购经理指数为([\d.]+)%", text)
    if pmi_match:
        current["manufacturingPmi"] = float(pmi_match.group(1))
    unemployment_match = re.search(rf"{month}月份，?全国城镇调查失业率为([\d.]+)%", text)
    if unemployment_match:
        current["unemployment"] = float(unemployment_match.group(1))

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


def update_pbc(data: dict) -> None:
    listing_url = "https://www.pbc.gov.cn/diaochatongjisi/116219/116225/index.html"
    listing = fetch_text(listing_url)
    links = re.findall(r'href="([^"]+)"[^>]+title="([^"]*金融统计数据报告)"', listing)
    latest_link = next(((href, title) for href, title in links if re.search(r"20\d{2}年\d+月金融统计数据报告", title)), None)
    if not latest_link:
        raise RuntimeError("未找到人民银行最新月度金融统计报告")
    href, title = latest_link
    source_url = urllib.parse.urljoin(listing_url, href)
    text = strip_html(fetch_text(source_url))
    period_match = re.search(r"(20\d{2})年(\d+)月金融统计数据报告", title)
    period = f"{int(period_match.group(1)):04d}-{int(period_match.group(2)):02d}" if period_match else ""

    def number(pattern: str, default: float = 0.0) -> float:
        match = re.search(pattern, text)
        return float(match.group(1)) if match else default

    def signed(pattern: str, default: float = 0.0) -> float:
        match = re.search(pattern, text)
        if not match:
            return default
        direction, value = match.groups()
        return -float(value) if direction in {"下降", "减少"} else float(value)

    credit = data.get("credit") or {}
    credit.update({
        "period": period,
        "sourceUrl": source_url,
        "source": "中国人民银行",
        "socialFinanceStock": number(r"社会融资规模存量为([\d.]+)万亿元", credit.get("socialFinanceStock", 0)),
        "socialFinanceYoy": signed(r"社会融资规模存量为[\d.]+万亿元，同比(增长|下降)([\d.]+)%", credit.get("socialFinanceYoy", 0)),
        "socialFinanceYtd": number(r"前[一二三四五六七八九十]+个月社会融资规模增量累计为([\d.]+)万亿元", credit.get("socialFinanceYtd", 0)),
        "m2": number(r"广义货币（M2）余额([\d.]+)万亿元", credit.get("m2", 0)),
        "m2Yoy": signed(r"广义货币（M2）余额[\d.]+万亿元，同比(增长|下降)([\d.]+)%", credit.get("m2Yoy", 0)),
        "m1": number(r"狭义货币（M1）余额([\d.]+)万亿元", credit.get("m1", 0)),
        "m1Yoy": signed(r"狭义货币（M1）余额[\d.]+万亿元，同比(增长|下降)([\d.]+)%", credit.get("m1Yoy", 0)),
        "rmbLoansYtd": number(r"前[一二三四五六七八九十]+个月人民币贷款增加([\d.]+)万亿元", credit.get("rmbLoansYtd", 0)),
        "householdLoansYtd": signed(r"住户贷款(增加|减少)([\d.]+)万亿元", credit.get("householdLoansYtd", 0)),
        "corporateLoansYtd": signed(r"企（事）业单位贷款(增加|减少)([\d.]+)万亿元", credit.get("corporateLoansYtd", 0)),
        "interbankRate": number(r"同业拆借加权平均利率为([\d.]+)%", credit.get("interbankRate", 0)),
        "repoRate": number(r"质押式回购加权平均利率为([\d.]+)%", credit.get("repoRate", 0)),
    })
    data["credit"] = credit


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


def fred_series(series_id: str, limit: int | None = None) -> list[dict]:
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
    return rows[-limit:] if limit else rows


def fred_chart_history(rows: list[dict], key: str) -> list[dict]:
    """Keep a readable long-run chart without bloating the daily calculation series."""
    if not rows:
        return []
    if key == "nfci":
        selected = rows
        bucket = lambda day: day[:7]  # one observation per month across the full history
    else:
        last_day = dt.date.fromisoformat(rows[-1]["date"])
        cutoff = (last_day - dt.timedelta(days=366 * 5)).isoformat()
        selected = [item for item in rows if item["date"] >= cutoff]
        bucket = lambda day: dt.date.fromisoformat(day).isocalendar()[:2]  # one observation per week
    sampled = {}
    for item in selected:
        sampled[bucket(item["date"])] = item
    result = list(sampled.values())
    if result and result[-1]["date"] != rows[-1]["date"]:
        result.append(rows[-1])
    return result


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
            "values": rows[-520:],
            "longValues": fred_chart_history(rows, key),
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
        china_now = dt.datetime.now(dt.timezone(dt.timedelta(hours=8)))
        if china_now.time() < dt.time(15, 10):
            # Daily k-lines expose the still-growing current-session amount intraday.
            # Exclude it so the temperature never compares a partial day with full days.
            values = [item for item in values if item["date"] < china_now.date().isoformat()]
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


def xlsx_rows(raw: bytes) -> list[list[str]]:
    """Read the simple worksheet returned by SZSE without third-party packages."""
    ns = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        names = set(archive.namelist())
        shared = []
        if "xl/sharedStrings.xml" in names:
            root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
            shared = ["".join(node.text or "" for node in item.iter(f"{ns}t")) for item in root.findall(f"{ns}si")]
        sheet_name = next((name for name in sorted(names) if name.startswith("xl/worksheets/sheet") and name.endswith(".xml")), None)
        if not sheet_name:
            raise RuntimeError("深交所工作表缺失")
        root = ET.fromstring(archive.read(sheet_name))
        result = []
        for row in root.iter(f"{ns}row"):
            cells = {}
            for cell in row.findall(f"{ns}c"):
                ref = cell.attrib.get("r", "")
                column = re.match(r"[A-Z]+", ref)
                if not column:
                    continue
                cell_type = cell.attrib.get("t")
                inline = cell.find(f"{ns}is")
                value_node = cell.find(f"{ns}v")
                if inline is not None:
                    value = "".join(node.text or "" for node in inline.iter(f"{ns}t"))
                elif value_node is None:
                    value = ""
                elif cell_type == "s":
                    value = shared[int(value_node.text)]
                else:
                    value = value_node.text or ""
                cells[column.group(0)] = value
            if cells:
                result.append([cells.get(chr(code), "") for code in range(ord("A"), ord("I"))])
        return result


def number_value(value) -> float:
    text = str(value or "").strip().replace(",", "")
    return float(text) if text not in {"", "-", "--"} else 0.0


def sse_margin(day: str) -> tuple[float, float]:
    compact = day.replace("-", "")
    balance = 0.0
    buy = 0.0
    total = None
    seen = 0
    for page_no in (1, 2, 3):
        params = urllib.parse.urlencode({
            "isPagination": "true", "tabType": "mxtype", "detailsDate": compact,
            "pageHelp.pageSize": 2000, "pageHelp.pageNo": page_no,
            "pageHelp.beginPage": page_no, "pageHelp.endPage": page_no,
        })
        payload = json.loads(fetch_text(
            f"https://query.sse.com.cn/marketdata/tradedata/queryMargin.do?{params}",
            headers={"Referer": "https://www.sse.com.cn/"},
        ))
        page = payload.get("pageHelp") or {}
        rows = page.get("data") or payload.get("result") or []
        if total is None:
            total = int(page.get("total") or len(rows))
        balance += sum(number_value(item.get("rzye")) for item in rows)
        buy += sum(number_value(item.get("rzmre")) for item in rows)
        seen += len(rows)
        if seen >= total or not rows:
            break
    if not seen:
        raise RuntimeError(f"上交所{day}无融资数据")
    return balance, buy


def szse_margin(day: str) -> tuple[float, float]:
    params = urllib.parse.urlencode({
        "SHOWTYPE": "xlsx", "CATALOGID": "1837_xxpl", "TABKEY": "tab2",
        "txtDate": day, "random": "0.1",
    })
    raw = fetch_bytes(
        f"https://www.szse.cn/api/report/ShowReport?{params}",
        headers={"Referer": "https://www.szse.cn/"},
    )
    rows = xlsx_rows(raw)
    data_rows = [row for row in rows if row and re.fullmatch(r"\d{6}", str(row[0] or ""))]
    if not data_rows:
        raise RuntimeError(f"深交所{day}无融资数据")
    buy = sum(number_value(row[2]) for row in data_rows)
    balance = sum(number_value(row[3]) for row in data_rows)
    return balance, buy


def update_margin(data: dict) -> list[str]:
    """Refresh recent official SSE/SZSE financing data and preserve older points."""
    series = data.setdefault("series", {})
    existing = {item["date"]: item for item in (series.get("marginBalance") or {}).get("values", []) if item.get("date")}
    warnings = []
    today = dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).date()
    successful = 0
    for offset in range(1, 19):
        day = today - dt.timedelta(days=offset)
        if day.weekday() >= 5:
            continue
        iso = day.isoformat()
        if iso in existing and successful >= 2:
            successful += 1
            if successful >= 8:
                break
            continue
        try:
            sh_balance, sh_buy = sse_margin(iso)
            sz_balance, sz_buy = szse_margin(iso)
            existing[iso] = {
                "date": iso,
                "value": round((sh_balance + sz_balance) / 1e12, 4),
                "buy": round((sh_buy + sz_buy) / 1e9, 2),
            }
            successful += 1
        except Exception:
            continue
        if successful >= 8:
            break
        time.sleep(0.2)
    values = sorted(existing.values(), key=lambda item: item["date"])[-120:]
    if not values:
        warnings.append("沪深交易所两融数据暂不可用")
        return warnings
    series["marginBalance"] = {
        "label": "沪深融资余额",
        "unit": "万亿元",
        "meaning": "杠杆资金存量；连续上升代表风险偏好增强，也要警惕过快堆积",
        "source": "上海证券交易所、深圳证券交易所",
        "sourceUrl": "https://www.sse.com.cn/market/othersdata/margin/detail/",
        "secondarySourceUrl": "https://www.szse.cn/disclosure/margin/object/index.html",
        "values": values,
    }
    return warnings


def update_course_sectors(data: dict) -> list[str]:
    """Use liquid ETF prices only as market proxies for the course's focus areas."""
    specs = [
        ("aiHardware", "AI硬件", "1.588170", "科创半导体ETF代理", "主攻", "设备材料、存储/HBM、PCB/CCL、先进封装；光模块不追高", "看订单、利润、估值和资金是否同时确认"),
        ("innovativeDrug", "创新药", "0.159992", "创新药ETF代理", "主攻", "从估值修复进入产业验证，关注BD、出海与商业化", "看授权交易、临床进度和现金流"),
        ("aiApplication", "AI应用", "1.513330", "恒生互联网ETF代理", "主攻", "关注AI赋能传统业务、原生AI与Agent", "平台股价格只能近似反映应用风险偏好"),
        ("nonferrous", "有色金属", "1.512400", "有色金属ETF代理", "周期", "供需与价格驱动的进攻方向", "看铜铝等价格、库存与企业利润"),
        ("gold", "黄金", "1.518880", "黄金ETF代理", "底仓", "宏观对冲与防守资产", "看实际利率、美元和央行需求"),
        ("robot", "人形机器人", "1.562500", "机器人ETF代理", "卫星", "长期方向较好，但确定性低于成熟硬件链", "小仓位观察订单与量产，不让卫星仓变主仓"),
        ("space", "商业航天", "1.512660", "军工ETF宽口径代理", "卫星", "产业方向值得跟踪，但ETF映射并不纯", "重点验证发射、订单和产业资本开支"),
    ]
    previous = {item.get("key"): item for item in data.get("courseSectors", [])}
    result = []
    warnings = []
    for key, name, secid, proxy, role, thesis, validation in specs:
        try:
            rows = eastmoney_kline(secid, 90)
            change5 = pct_change(rows, 5)
            change20 = pct_change(rows, 20)
            if change20 >= 12 or change5 >= 10 or change5 <= -7:
                state, tone, reading = "风险", "risk", "涨幅拥挤或短线明显转弱，先等估值和基本面消化。"
            elif change5 > 1 and 0 <= change20 < 8:
                state, tone, reading = "机会", "opportunity", "短期修复但尚未明显过热，可进入验证清单。"
            else:
                state, tone, reading = "观察", "observe", "价格信号未形成清晰共振，继续等订单、利润或资金确认。"
            result.append({
                "key": key, "name": name, "proxy": proxy, "role": role,
                "value": rows[-1]["value"], "date": rows[-1]["date"],
                "change5": round1(change5), "change20": round1(change20),
                "state": state, "tone": tone, "reading": reading,
                "thesis": thesis, "validation": validation,
                "source": "东方财富公开行情（市场代理）",
            })
        except Exception:
            if key in previous:
                result.append(previous[key])
            else:
                warnings.append(f"{name}市场代理暂不可用")
        time.sleep(0.25)
    data["courseSectors"] = result
    return warnings


def build_market_pulse(data: dict) -> None:
    margin_values = (data.get("series", {}).get("marginBalance") or {}).get("values") or []
    current = margin_values[-1] if margin_values else {"date": "—", "value": 0, "buy": 0}
    margin_change5 = pct_change(margin_values, min(5, max(1, len(margin_values) - 1))) if len(margin_values) > 1 else 0
    if margin_change5 >= 2.5:
        margin_state, margin_tone = "升温较快", "risk"
    elif margin_change5 > 0:
        margin_state, margin_tone = "温和增加", "opportunity"
    else:
        margin_state, margin_tone = "杠杆降温", "observe"
    data["marketPulse"] = {
        "summary": "成交额看参与度，两融看杠杆意愿，ETF申赎看资金正在投票的方向；三者需要一起看。",
        "margin": {
            "date": current.get("date"), "value": current.get("value"), "buy": current.get("buy"),
            "change5": round1(margin_change5), "state": margin_state, "tone": margin_tone,
            "interpretation": "融资余额持续温和上升有利于行情扩散；若短期陡增而指数不涨，反而要警惕拥挤和承接转弱。",
        },
        "etfFlow": {
            "date": "2026-09-21", "all": -415.2, "stock": -649.0, "unit": "亿元",
            "state": "股票ETF显著净流出", "tone": "risk",
            "inflows": ["科创半导体ETF华夏 +11.45亿元", "科创50ETF华夏 +4.88亿元", "半导体设备ETF国泰 +3.75亿元"],
            "outflows": ["中证500ETF南方 -9.10亿元", "上证50ETF华夏 -8.10亿元", "创业板ETF易方达 -7.84亿元"],
            "interpretation": "总量净流出说明增量资金偏谨慎，但半导体方向仍获局部申购；这是结构性资金偏好，不等于全市场转强。",
            "source": "东方财富Choice估算",
            "sourceUrl": "https://finance.eastmoney.com/a/202609223880602526.html",
        },
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

    def absolute_change(key: str, sessions: int = 20) -> float:
        values = series_until(series, key)
        if len(values) <= sessions:
            return 0.0
        return float(values[-1]["value"]) - float(values[-sessions - 1]["value"])

    def threshold_signal(value: float, risk_line: float, opportunity_line: float) -> str:
        if value >= risk_line:
            return "已触及风险线"
        if value <= opportunity_line:
            return "已触及机会线"
        return "处于观察区间"

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
                "change20": round(absolute_change("effr"), 3), "riskLine": 4.5, "opportunityLine": 2.5,
                "thresholdSignal": threshold_signal(effr, 4.5, 2.5),
                "opportunity": "继续下行意味着现金与融资成本缓解，风险资产估值可获得支撑。",
                "risk": "若维持高位或重新上行，说明政策利率层面的宽松仍不充分。",
            },
            {
                "key": "real10y", "name": "美国10年实际利率", "value": real10y, "unit": "%", "date": real_date,
                "score": round1(scores["real10y"]), "status": real_state[0], "tone": real_state[1], "meaning": "长期真实资金成本 / 成长股折现率", "interpretation": real_state[2],
                "change20": round(absolute_change("real10y"), 3), "riskLine": 2.0, "opportunityLine": 1.2,
                "thresholdSignal": threshold_signal(real10y, 2.0, 1.2),
                "opportunity": "持续回落通常利好黄金、成长股及其他长久期资产。",
                "risk": "高位或再创新高会压缩高估值资产的容错空间。",
            },
            {
                "key": "broadDollar", "name": "广义美元指数", "value": broad, "unit": "", "date": broad_date,
                "score": round1(scores["broadDollar"]), "status": dollar_state[0], "tone": dollar_state[1], "meaning": "美元相对全球货币的强弱 / 全球美元压力", "interpretation": dollar_state[2],
                "change20": round(absolute_change("broadDollar"), 3), "riskLine": 125.0, "opportunityLine": 118.0,
                "thresholdSignal": threshold_signal(broad, 125.0, 118.0),
                "opportunity": "美元走弱或平稳时，新兴市场、大宗商品与非美风险资产压力减轻。",
                "risk": "20日快速升值超过约2%时，需要警惕全球流动性收缩。",
            },
            {
                "key": "nfci", "name": "NFCI 金融状况指数", "value": nfci, "unit": "", "date": nfci_date,
                "score": round1(scores["nfci"]), "status": nfci_state[0], "tone": nfci_state[1], "meaning": "美国金融体系综合松紧", "interpretation": nfci_state[2],
                "change20": round(absolute_change("nfci"), 3), "riskLine": 0.0, "opportunityLine": -0.5,
                "thresholdSignal": threshold_signal(nfci, 0.0, -0.5),
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
    score = round1(score)
    label, action, explanation = temperature_label(score)
    def decision_position(value: float, cautious_line: float, bold_line: float, higher_is_better: bool) -> float:
        """Map raw values to one shared action scale: cautious 0–35, observe 35–65, bold 65–100."""
        distance = abs(bold_line - cautious_line) or 1.0
        if higher_is_better:
            position = 35 + (value - cautious_line) / distance * 30
        else:
            position = 35 + (cautious_line - value) / distance * 30
        return round1(clamp(position))

    def decision_zone(position: float) -> tuple[str, str]:
        if position < 35:
            return "谨慎", "cautious"
        if position < 65:
            return "观察", "observe"
        return "大胆", "bold"

    def history_note(key: str, current: float, higher_is_better: bool, window: int = 520) -> str:
        values = (series.get(key) or {}).get("values") or []
        selected = values[-window:]
        percentile = percentile_rank(selected, current)
        direction = "越高越友好" if higher_is_better else "越低越友好"
        return f"近{len(selected)}期原始值第{percentile:.0f}百分位 · {direction}"

    def component(
        name: str,
        key: str,
        reading: str,
        current: float,
        cautious_line: float,
        bold_line: float,
        higher_is_better: bool,
        cautious_label: str,
        bold_label: str,
        percentile: str,
        interpretation: str,
        weight: int,
    ) -> dict:
        position = decision_position(current, cautious_line, bold_line, higher_is_better)
        zone, tone = decision_zone(position)
        return {
            "name": name,
            "key": key,
            "reading": reading,
            "position": position,
            "zone": zone,
            "tone": tone,
            "cautiousLabel": cautious_label,
            "boldLabel": bold_label,
            "percentile": percentile,
            "interpretation": interpretation,
            "weight": weight,
        }

    effr = latest(series, "effr", 4.0)
    real10y = latest(series, "real10y", 2.0)
    broad_dollar = latest(series, "broadDollar", 120.0)
    nfci = latest(series, "nfci", 0.0)
    turnover = latest(series, "turnover", 0.0)
    oil_change20 = pct_change(oil_values, 20)

    oil_change_history = []
    for idx in range(20, len(oil_values)):
        start = float(oil_values[idx - 20]["value"])
        end = float(oil_values[idx]["value"])
        if start:
            oil_change_history.append({"date": oil_values[idx]["date"], "value": (end / start - 1) * 100})
    oil_percentile = percentile_rank(oil_change_history[-260:], oil_change20)

    industrial_position = decision_position(float(china.get("industrial", 0)), 4.0, 6.0, True)
    retail_position = decision_position(float(china.get("retail", 0)), 2.0, 5.0, True)
    growth_position = round1((industrial_position + retail_position) / 2)
    growth_zone, growth_tone = decision_zone(growth_position)

    components = [
        component("短端利率", "effr", f"EFFR {effr:.2f}%", effr, 4.5, 2.5, False,
                  "谨慎 ≥ 4.5%", "大胆 ≤ 2.5%", history_note("effr", effr, False),
                  "短端资金越便宜，现金和融资成本压力越小。", 10),
        component("真实利率", "real10y", f"10年实际利率 {real10y:.2f}%", real10y, 2.0, 1.2, False,
                  "谨慎 ≥ 2.0%", "大胆 ≤ 1.2%", history_note("real10y", real10y, False),
                  "真实利率越高，成长股和长久期资产的估值压力越大。", 10),
        component("美元强弱", "broadDollar", f"广义美元 {broad_dollar:.2f}", broad_dollar, 125.0, 118.0, False,
                  "谨慎 ≥ 125", "大胆 ≤ 118", history_note("broadDollar", broad_dollar, False),
                  "美元越强，全球非美资产通常越容易承受资金压力。", 10),
        component("金融松紧", "nfci", f"NFCI {nfci:+.3f}", nfci, 0.0, -0.5, False,
                  "谨慎 ≥ 0", "大胆 ≤ -0.5", history_note("nfci", nfci, False),
                  "NFCI为负代表金融条件比长期平均更宽松。", 10),
        {
            "name": "国内增长", "key": "growth", "reading": f"工业 {china['industrial']:+.1f}% / 消费 {china['retail']:+.1f}%",
            "position": growth_position, "zone": growth_zone, "tone": growth_tone,
            "cautiousLabel": "谨慎：工业≤4%或消费≤2%", "boldLabel": "大胆：工业≥6%且消费≥5%",
            "percentile": "月度历史样本仍在积累 · 当前按工业与消费双确认",
            "interpretation": "增长要看工业和消费是否同时改善，单项走强不算全面复苏。", "weight": 25,
        },
        component("市场资金", "turnover", f"A股成交 {turnover:.2f}万亿元", turnover, 1.0, 2.5, True,
                  "谨慎 ≤ 1.0万亿", "大胆 ≥ 2.5万亿", history_note("turnover", turnover, True, 260),
                  "成交越活跃，风险偏好和市场承接力通常越强。", 25),
        component("通胀压力", "inflation", f"油价20日 {oil_change20:+.1f}%", oil_change20, 15.0, 5.0, False,
                  "谨慎 ≥ +15%", "大胆 ≤ +5%", f"近{len(oil_change_history[-260:])}期涨幅第{oil_percentile:.0f}百分位 · 涨得越慢越友好",
                  "油价短期急涨会抬高通胀预期，压缩降息和估值空间。", 10),
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

    def trend_delta(sessions: int) -> float:
        if not values:
            return 0.0
        start = values[-sessions - 1] if len(values) > sessions else values[0]
        return round1(score - start)

    def trend_direction(delta: float) -> str:
        if delta >= 1.0:
            return "right"
        if delta <= -1.0:
            return "left"
        return "flat"

    short_delta = trend_delta(5)
    medium_delta = trend_delta(20)
    short_direction = trend_direction(short_delta)
    medium_direction = trend_direction(medium_delta)
    if short_direction == "right" and medium_direction == "right":
        trend_label, trend_tone = "向右共振", "warming"
        trend_message = "短期和20日趋势同时向右，机会正在增加，但仍要结合所处温度区间。"
    elif short_direction == "right" and medium_direction == "left":
        trend_label, trend_tone = "短期右转", "mixed"
        trend_message = "短期已经向右，但20日趋势仍向左：更像修复，机会出现但需要继续确认。"
    elif short_direction == "left" and medium_direction == "right":
        trend_label, trend_tone = "短期左转", "mixed"
        trend_message = "短期正在向左，但20日趋势仍向右：更像升温过程中的回撤，先观察是否企稳。"
    elif short_direction == "left" and medium_direction == "left":
        trend_label, trend_tone = "向左共振", "cooling"
        trend_message = "短期和20日趋势同时向左，环境正在降温，应提高防守和等待确认。"
    else:
        trend_label, trend_tone = "方向未确认", "flat"
        trend_message = "当前方向不够一致，暂时按震荡看待，等待短期与20日趋势形成共振。"
    if score >= 80 and short_direction == "right":
        trend_label, trend_tone = "向右但已过热", "cooling"
        trend_message = "趋势仍向右，但已进入过热区；这时向右代表拥挤和追高风险，而不是新增机会。"

    strongest = max(components, key=lambda item: item["position"])
    weakest = min(components, key=lambda item: item["position"])
    data["temperature"] = {
        "score": round1(score),
        "label": label,
        "action": action,
        "explanation": explanation,
        "avg30": round1(avg30),
        "avg90": round1(avg90),
        "trend": {
            "label": trend_label,
            "tone": trend_tone,
            "message": trend_message,
            "shortDelta": short_delta,
            "mediumDelta": medium_delta,
            "shortDirection": short_direction,
            "mediumDirection": medium_direction,
        },
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


def percentile_rank(values: list[dict], current: float) -> float:
    numbers = [float(item["value"]) for item in values if item.get("value") is not None]
    if not numbers:
        return 50.0
    return round1(sum(value <= current for value in numbers) / len(numbers) * 100)


def build_timeline_events(data: dict) -> None:
    temperature = data["temperature"]
    history = temperature.get("history") or []
    series = data["series"]
    events = []

    def score_on(day: str) -> float:
        return value_on_or_before(history, day, temperature["score"])

    def add_event(day: str, event_type: str, indicator: str, title: str, detail: str, impact: str, source_url: str = "") -> None:
        try:
            event_day = dt.date.fromisoformat(day)
        except Exception:
            return
        for existing in reversed(events):
            if existing["indicator"] != indicator or existing["title"] != title:
                continue
            prior_day = dt.date.fromisoformat(existing["date"])
            if abs((event_day - prior_day).days) < 10:
                return
            break
        events.append({
            "id": f"{day}-{indicator}-{len(events)}",
            "date": day,
            "score": round1(score_on(day)),
            "type": event_type,
            "indicator": indicator,
            "title": title,
            "detail": detail,
            "impact": impact,
            "sourceUrl": source_url,
        })

    band_specs = [
        (35, "偏冷区", "防守优先"),
        (50, "均衡区", "风险预算可由防守转向精选"),
        (65, "偏热区", "风险偏好增强但不宜追高"),
        (80, "过热区", "拥挤与追高风险上升"),
    ]
    for previous, current in zip(history, history[1:]):
        before = float(previous["value"])
        after = float(current["value"])
        for threshold, zone, meaning in band_specs:
            if before < threshold <= after:
                event_type = "risk" if threshold == 80 else "opportunity"
                add_event(
                    current["date"], event_type, "宏观温度", f"温度向上进入{zone}",
                    f"综合温度由 {before:.1f} 升至 {after:.1f}，越过 {threshold} 分界线。",
                    meaning,
                )
            elif before >= threshold > after:
                event_type = "opportunity" if threshold == 80 else "risk"
                add_event(
                    current["date"], event_type, "宏观温度", f"温度向下离开{zone}",
                    f"综合温度由 {before:.1f} 降至 {after:.1f}，跌破 {threshold} 分界线。",
                    "离开过热区、拥挤度缓解" if threshold == 80 else "环境降温，仓位与节奏需要更谨慎",
                )

    threshold_specs = [
        ("effr", "EFFR", 4.5, 2.5, "短端美元资金成本"),
        ("real10y", "10年实际利率", 2.0, 1.2, "长期真实资金成本"),
        ("broadDollar", "广义美元", 125.0, 118.0, "全球美元压力"),
        ("nfci", "NFCI", 0.0, -0.5, "美国综合金融条件"),
    ]
    for key, label, risk_line, opportunity_line, meaning in threshold_specs:
        item = series.get(key) or {}
        values = (item.get("values") or [])[-260:]
        source_url = item.get("sourceUrl") or ""
        for previous, current in zip(values, values[1:]):
            before = float(previous["value"])
            after = float(current["value"])
            day = current["date"]
            if before < risk_line <= after:
                add_event(day, "risk", label, f"{label}升破风险线", f"{before:.3f} → {after:.3f}，越过风险线 {risk_line:g}。", f"{meaning}转紧，风险资产的容错空间下降。", source_url)
            elif before >= risk_line > after:
                add_event(day, "opportunity", label, f"{label}跌回风险线下", f"{before:.3f} → {after:.3f}，重新回到风险线 {risk_line:g} 以下。", f"{meaning}边际缓和，但还需观察是否持续。", source_url)
            if before > opportunity_line >= after:
                add_event(day, "opportunity", label, f"{label}触及机会线", f"{before:.3f} → {after:.3f}，下穿机会线 {opportunity_line:g}。", f"{meaning}进入较友好区间，可提高关注度。", source_url)
            elif before <= opportunity_line < after:
                add_event(day, "info", label, f"{label}离开机会线", f"{before:.3f} → {after:.3f}，重新回到机会线 {opportunity_line:g} 以上。", f"{meaning}的宽松程度减弱，需要等待新的确认。", source_url)

    effr = (series.get("effr") or {}).get("values") or []
    for previous, current in zip(effr[-260:], effr[-259:]):
        before = float(previous["value"])
        after = float(current["value"])
        delta = after - before
        if abs(delta) < 0.24:
            continue
        direction = "上升" if delta > 0 else "下降"
        event_type = "risk" if delta > 0 else "opportunity"
        add_event(
            current["date"], event_type, "EFFR", f"EFFR单次{direction} {abs(delta) * 100:.0f}bp",
            f"有效联邦基金利率由 {before:.2f}% 变为 {after:.2f}%。",
            "短端资金成本上升，流动性边际收紧。" if delta > 0 else "短端资金成本下降，流动性边际改善。",
            (series.get("effr") or {}).get("sourceUrl") or "",
        )

    events = sorted(events, key=lambda item: (item["date"], item["id"]))[-24:]
    data["timelineEvents"] = events


def build_china_report(data: dict) -> None:
    china = data["china"]
    history = sorted(data.get("chinaHistory") or [], key=lambda item: item.get("period", ""))
    previous = next((item for item in reversed(history) if item.get("period", "") < china.get("period", "")), None)

    industrial = float(china.get("industrial", 0))
    industrial_monthly = float(china.get("industrialMonthly", industrial))
    retail = float(china.get("retail", 0))
    retail_monthly = float(china.get("retailMonthly", retail))
    fixed_asset = float(china.get("fixedAsset", 0))
    fixed_asset_ex_property = float(china.get("fixedAssetExRealEstate", fixed_asset))
    real_estate = float(china.get("realEstate", 0))
    property_sales = float(china.get("realEstateSales", real_estate))
    high_tech = float(china.get("highTechMonthly", 0))
    profit = float(china.get("industrialProfit", 0))
    cpi = float(china.get("cpi", 0))
    core_cpi = float(china.get("coreCpi", cpi))
    ppi = float(china.get("ppi", 0))
    pmi = float(china.get("manufacturingPmi", 50))
    unemployment = float(china.get("unemployment", 0))
    exports = float(china.get("exportsMonthly", 0))

    production_strong = industrial_monthly >= 5 and high_tech >= 8
    demand_weak = retail < 3 or retail_monthly < 2
    investment_weak = fixed_asset < 0
    property_drag = real_estate <= -10
    if production_strong and demand_weak and investment_weak:
        stance = "结构性复苏"
        tone = "mixed"
        title = "生产强、需求弱：更像结构行情，不是全面复苏"
    elif retail >= 4 and fixed_asset >= 2 and industrial >= 5:
        stance = "广泛复苏"
        tone = "positive"
        title = "生产、消费和投资形成共振，复苏正在扩散"
    elif industrial < 4 and retail < 2 and fixed_asset < 0:
        stance = "需求降温"
        tone = "negative"
        title = "生产与内需同时偏弱，防守仍比扩张重要"
    else:
        stance = "分化运行"
        tone = "neutral"
        title = "不同部门方向不一，需要等待更广泛的数据确认"

    def change_text(key: str, label: str) -> str | None:
        if not previous or key not in previous:
            return None
        delta = float(china.get(key, 0)) - float(previous.get(key, 0))
        if abs(delta) < 0.05:
            return f"{label}持平"
        return f"{label}{'改善' if delta > 0 else '走弱'}{abs(delta):.1f}个百分点"

    changes = [item for item in [
        change_text("industrial", "工业"),
        change_text("retail", "消费"),
        change_text("fixedAsset", "固投"),
        change_text("realEstate", "地产投资"),
    ] if item]
    trend_summary = "较上期：" + "；".join(changes) + "。" if changes else "历史月度样本仍在积累，暂不对单月方向做过度外推。"

    summary = (
        f"供给端明显强于需求端：当月工业增加值 {industrial_monthly:+.1f}%，高技术制造业 {high_tech:+.1f}%，"
        f"但当月社零只有 {retail_monthly:+.1f}%，固定资产投资累计 {fixed_asset:+.1f}%，房地产开发投资 {real_estate:+.1f}%。"
        "这组数据说明增长主要由先进制造和外需支撑，居民与企业的广泛需求尚未形成共振。"
    )
    contradiction = (
        f"强项是高技术制造业 {high_tech:+.1f}% 和出口 {exports:+.1f}%；"
        f"弱项是消费累计 {retail:+.1f}%、剔除地产后的投资 {fixed_asset_ex_property:+.1f}% 以及地产销售额 {property_sales:+.1f}%。"
    )

    data["chinaReport"] = {
        "period": china.get("period"),
        "releaseDate": china.get("releaseDate"),
        "stance": stance,
        "tone": tone,
        "title": title,
        "summary": summary,
        "contradiction": contradiction,
        "trendSummary": trend_summary,
        "signals": [
            {
                "label": "生产与产业升级", "status": "有韧性" if production_strong else "待确认", "tone": "positive" if production_strong else "neutral",
                "data": f"工业当月 {industrial_monthly:+.1f}% · 高技术制造 {high_tech:+.1f}%",
                "analysis": "工业仍在扩张，高技术制造明显快于整体，说明产业升级是当前最清晰的增长支点。" if production_strong else "生产端尚未形成稳定扩张，需要继续观察工业和高技术制造能否同步改善。",
            },
            {
                "label": "居民消费", "status": "偏弱" if demand_weak else "改善", "tone": "negative" if demand_weak else "positive",
                "data": f"社零当月 {retail_monthly:+.1f}% · 累计 {retail:+.1f}%",
                "analysis": "消费扩张速度偏低，家庭部门的信心与收入预期仍不足，暂不能把生产强势等同于内需全面修复。" if demand_weak else "消费增速已回到较健康区间，生产改善正向居民需求扩散。",
            },
            {
                "label": "固定资产投资", "status": "收缩" if investment_weak else "扩张", "tone": "negative" if investment_weak else "positive",
                "data": f"整体 {fixed_asset:+.1f}% · 剔除地产 {fixed_asset_ex_property:+.1f}%",
                "analysis": "剔除房地产后仍为负，说明投资偏弱并不只是地产问题，企业扩产和地方项目仍需政策与订单确认。" if fixed_asset_ex_property < 0 else "非地产投资保持扩张，说明实体资本开支已有一定支撑。",
            },
            {
                "label": "房地产", "status": "主要拖累" if property_drag else "拖累缓和", "tone": "negative" if property_drag else "neutral",
                "data": f"开发投资 {real_estate:+.1f}% · 销售额 {property_sales:+.1f}%",
                "analysis": "开发投资和销售仍深度收缩，地产通过财富效应、信用和地方财政继续压制总需求。" if property_drag else "地产拖累正在减轻，但仍需销售、价格和投资连续改善才能确认筑底。",
            },
            {
                "label": "企业景气与利润", "status": "利润修复、景气分化", "tone": "mixed",
                "data": f"制造业PMI {pmi:.1f} · 工业利润 {profit:+.1f}%（至{china.get('profitPeriod', '—')}）",
                "analysis": "利润数据改善，但PMI仍低于50，说明盈利修复尚未扩散到足够多的企业；同时利润数据比月度运行数据滞后一期。" if pmi < 50 else "PMI回到扩张区间并伴随利润改善，企业景气的广度正在增强。",
            },
            {
                "label": "价格与就业", "status": "通胀温和", "tone": "neutral",
                "data": f"CPI {cpi:+.1f}% · 核心CPI {core_cpi:+.1f}% · PPI {ppi:+.1f}% · 失业率 {unemployment:.1f}%",
                "analysis": "居民端通胀仍温和，政策重心仍可偏向稳增长；但PPI回升意味着上游成本正在抬头，需要留意利润是否被成本侵蚀。",
            },
        ],
        "assetImplications": [
            {"label": "权益市场", "tone": "mixed", "title": "结构机会大于全面机会", "text": "高技术制造、出口和利润修复提供支撑；消费、投资和地产仍弱，暂不足以确认所有行业盈利同步上行。"},
            {"label": "利率与债券", "tone": "neutral", "title": "内需弱提供支撑，价格回升形成制约", "text": "消费和投资偏弱通常有利于宽松预期，但PPI回升后，不宜只依据弱需求做单方向判断。"},
            {"label": "家庭决策", "tone": "neutral", "title": "分批、分结构，不把生产强等同全面复苏", "text": "在消费与地产出现连续改善前，风险预算更适合逐步增加，并优先验证盈利、现金流与订单。"},
        ],
        "watchPoints": [
            {"label": "内需确认", "condition": "社零当月与累计增速同时回到3%以上", "met": retail_monthly >= 3 and retail >= 3},
            {"label": "投资止跌", "condition": "剔除地产投资转正，地产投资降幅连续收窄", "met": fixed_asset_ex_property > 0 and real_estate > -10},
            {"label": "景气扩散", "condition": "制造业PMI回到50以上，工业与消费同时改善", "met": pmi >= 50 and industrial >= 5 and retail >= 3},
        ],
        "sourceUrl": china.get("sourceUrl"),
        "profitSourceUrl": china.get("profitSourceUrl"),
    }


def build_daily_report(data: dict) -> None:
    series = data["series"]
    temperature = data["temperature"]
    trend = temperature["trend"]
    liquidity = data["liquidity"]
    china = data["china"]

    turnover_values = (series.get("turnover") or {}).get("values") or []
    real_values = (series.get("real10y") or {}).get("values") or []
    nfci_values = (series.get("nfci") or {}).get("values") or []
    temp_values = temperature.get("history") or []
    turnover = latest(series, "turnover", 0)
    real10y = latest(series, "real10y", 0)
    nfci = latest(series, "nfci", 0)
    temp_pct = percentile_rank(temp_values, temperature["score"])
    turnover_pct = percentile_rank(turnover_values[-260:], turnover)
    real_pct = percentile_rank(real_values[-520:], real10y)
    nfci_pct = percentile_rank(nfci_values[-520:], nfci)

    if trend["shortDirection"] == "right" and trend["mediumDirection"] == "left":
        title = "短期修复已经启动，中期趋势还没完全转向"
    elif trend["shortDirection"] == "right" and trend["mediumDirection"] == "right":
        title = "短中期同时升温，机会正在扩散"
    elif trend["shortDirection"] == "left" and trend["mediumDirection"] == "left":
        title = "环境持续降温，先保护本金和节奏"
    else:
        title = "方向仍有分歧，等待数据形成共振"

    summary = (
        f"今天的综合温度是 {temperature['score']:.1f} 分，处在过去记录的第 {temp_pct:.0f} 百分位。"
        f"5日变化 {trend['shortDelta']:+.1f} 分，20日变化 {trend['mediumDelta']:+.1f} 分。"
        f"资金面主要由{temperature['strongest']}支撑，{temperature['weakest']}仍是最明显的约束。"
    )
    bottom_line = trend["message"] + " 这不是涨跌预测，而是今天应该承担多少风险的依据。"
    today = dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).date().isoformat()
    data["dailyReport"] = {
        "date": today,
        "publishTime": "每天 08:30（北京时间）",
        "title": title,
        "summary": summary,
        "bottomLine": bottom_line,
        "support": temperature["strongest"],
        "drag": temperature["weakest"],
        "evidence": [
            {"label": "综合温度", "value": f"{temperature['score']:.1f}分", "context": f"过去记录第 {temp_pct:.0f} 百分位；5日 {trend['shortDelta']:+.1f} / 20日 {trend['mediumDelta']:+.1f}", "tone": "neutral"},
            {"label": "A股成交额", "value": f"{turnover:.2f}万亿元", "context": f"过去260个交易日第 {turnover_pct:.0f} 百分位，越高代表市场参与度越强", "tone": "positive" if turnover_pct >= 60 else "neutral"},
            {"label": "10年实际利率", "value": f"{real10y:.2f}%", "context": f"过去520期第 {real_pct:.0f} 百分位，越高越压制高估值资产", "tone": "negative" if real_pct >= 70 else "neutral"},
            {"label": "NFCI", "value": f"{nfci:+.3f}", "context": f"过去520期第 {nfci_pct:.0f} 百分位；负值代表金融条件比长期平均更宽松", "tone": "positive" if nfci < -0.25 else "neutral"},
            {"label": "国内增长", "value": f"工业 {china['industrial']:+.1f}%", "context": f"{china['period']} 数据；消费 {china['retail']:+.1f}% / 固投 {china['fixedAsset']:+.1f}%", "tone": "neutral"},
            {"label": "美元流动性", "value": f"{liquidity['score']:.1f}分", "context": liquidity["summary"], "tone": "positive" if liquidity["score"] >= 55 else "neutral"},
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
        update_pbc(data)
    except Exception as exc:
        errors.append(f"人民银行信用数据更新失败：{exc}")
    try:
        warnings.extend(update_fred_liquidity(data))
    except Exception as exc:
        errors.append(f"FRED流动性更新失败：{exc}")
    try:
        warnings.extend(update_market(data))
    except Exception as exc:
        errors.append(f"行情更新失败：{exc}")
    try:
        warnings.extend(update_margin(data))
    except Exception as exc:
        warnings.append(f"沪深两融更新失败：{exc}")
    try:
        warnings.extend(update_course_sectors(data))
    except Exception as exc:
        warnings.append(f"课程重点行业代理更新失败：{exc}")

    if data.get("series"):
        build_liquidity(data)
        build_temperature(data)
        build_timeline_events(data)
        build_china_report(data)
        build_market_pulse(data)
        build_daily_report(data)
    now = dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).replace(microsecond=0)
    data["meta"] = {
        "updatedAt": now.isoformat(),
        "status": "ok" if not errors and not warnings else "partial",
        "errors": errors,
        "warnings": warnings,
        "note": "自动更新公开数据；付费课程原文与附件未发布。",
    }
    DATA_PATH.parent.mkdir(parents=True, exist_ok=True)
    DATA_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"updatedAt": data["meta"]["updatedAt"], "status": data["meta"]["status"], "errors": errors, "warnings": warnings}, ensure_ascii=False))


if __name__ == "__main__":
    main()
