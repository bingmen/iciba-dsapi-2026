# iciba-dsapi-all-0.5.1.py
# github.com/shenjuexiao
# 20261115

# iciba-dsapi-all.py
#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
iciba-dsapi-all.py

下载金山词霸每日一句（dsapi）历史数据归档 JSON，并生成年度汇总 JSON 与 README Markdown。
固定处理最近 DEFAULT_DAYS 天（含当天，Asia/Shanghai 时区），不接受命令行参数。
"""

import json
import logging
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

# 输出路径基准目录（脚本所在目录）
BASE_DIR = Path(__file__).parent

# API 基础地址
API_URL = "http://open.iciba.com/dsapi/?date={date}"

# 时区
TZ = ZoneInfo("Asia/Shanghai")

# 最早允许的日期
MIN_DATE = datetime(2018, 1, 1, tzinfo=TZ).date()

# 每次运行处理的日期天数（含当天共 10 天）
DEFAULT_DAYS = 10

# 网络请求重试间隔（秒）
RETRY_DELAYS = [1, 2, 4]

# 日志配置
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("iciba-dsapi")


def today_shanghai():
    """返回 Asia/Shanghai 时区的当前日期（date 对象）。"""
    return datetime.now(TZ).date()


def now_str_shanghai():
    """返回 Asia/Shanghai 时区的当前时间格式化字符串。"""
    return datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S")


def resolve_date_range():
    """
    固定返回最近 DEFAULT_DAYS 天的日期范围（含当天）。
    enddate = 今天，startdate = enddate - (DEFAULT_DAYS - 1)。
    若 startdate 早于 MIN_DATE，则裁剪到 MIN_DATE。
    """
    today = today_shanghai()
    enddate = today
    startdate = enddate - timedelta(days=DEFAULT_DAYS - 1)
    if startdate < MIN_DATE:
        startdate = MIN_DATE
    return startdate, enddate


def iter_dates(startdate, enddate):
    """生成 [startdate, enddate] 闭区间内的每日 date 序列。"""
    cur = startdate
    while cur <= enddate:
        yield cur
        cur += timedelta(days=1)


def year_dir(year):
    """返回指定年份的单日 JSON 归档目录 Path 对象。"""
    return BASE_DIR / f"dsapi-json-{year}"


def year_json_path(year):
    """返回指定年份的汇总 JSON 文件 Path 对象。"""
    return BASE_DIR / f"dsapi-json-{year}.json"


def year_md_path(year):
    """返回指定年份的 README Markdown 文件 Path 对象。"""
    return BASE_DIR / f"README-dsapi-{year}.md"


def find_daily_json(year, date_str):
    """
    在 dsapi-json-{year}/ 中查找任意 dsapi-*-{date}.json 文件。
    存在则返回第一个匹配的 Path，否则返回 None。
    """
    d = year_dir(year)
    if not d.is_dir():
        return None
    for p in d.glob(f"dsapi-*-{date_str}.json"):
        if p.is_file():
            return p
    return None


def fetch_daily_json(date_str):
    """
    请求 API 获取指定日期的 JSON 对象。
    最多重试 3 次（间隔 1s/2s/4s），全部失败返回 None。
    """
    url = API_URL.format(date=date_str)
    last_err = None
    for attempt, delay in enumerate([0] + RETRY_DELAYS):
        if delay:
            time.sleep(delay)
        try:
            resp = requests.get(url, timeout=15)
            if resp.status_code != 200:
                last_err = f"HTTP {resp.status_code}"
                logger.warning("请求 %s 失败（第 %d 次）：%s", url, attempt + 1, last_err)
                continue
            return resp.json()
        except (requests.RequestException, ValueError) as e:
            last_err = str(e)
            logger.warning("请求 %s 失败（第 %d 次）：%s", url, attempt + 1, last_err)
    logger.error("请求 %s 最终失败：%s", url, last_err)
    return None


def download_archives(startdate, enddate):
    """
    下载 [startdate, enddate] 区间内所有单日 JSON 归档。
    已存在对应文件的日期跳过；最终失败的日期记录日志并继续。
    返回实际写入的新文件 Path 列表。
    """
    new_files = []
    for d in iter_dates(startdate, enddate):
        date_str = d.strftime("%Y-%m-%d")
        year = d.strftime("%Y")

        # 已存在则跳过请求
        existing = find_daily_json(year, date_str)
        if existing is not None:
            logger.info("跳过已存在：%s", existing.name)
            continue

        data = fetch_daily_json(date_str)
        if data is None:
            logger.error("最终失败，跳过日期：%s", date_str)
            continue

        sid = str(data.get("sid") or "0000")
        out_dir = year_dir(year)
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"dsapi-{sid}-{date_str}.json"

        try:
            out_path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except OSError as e:
            logger.error("写入 %s 失败：%s", out_path, e)
            continue

        logger.info("已写入：%s", out_path.name)
        new_files.append(out_path)

    return new_files


def rebuild_year_json(year):
    """
    全量重建 dsapi-json-{year}.json。
    扫描 dsapi-json-{year}/dsapi-{sid}-{date}.json，
    主排序 sid 升序（不去重），缺失 sid 时按 date 升序。
    """
    d = year_dir(year)
    if not d.is_dir():
        logger.warning("目录不存在，跳过汇总：%s", d)
        return

    records = []
    for p in sorted(d.glob("dsapi-*-*.json")):
        if not p.is_file():
            continue
        try:
            obj = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            logger.error("读取 %s 失败：%s", p, e)
            continue
        records.append(obj)

    def sort_key(obj):
        """排序键：(sid 升序, date 升序)，sid 缺失时视为空串。"""
        sid = obj.get("sid")
        sid_key = str(sid) if sid else ""
        dateline = str(obj.get("dateline") or "")
        return (sid_key, dateline)

    records.sort(key=sort_key)

    out = year_json_path(year)
    try:
        out.write_text(
            json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        logger.info("已生成汇总：%s（%d 条）", out.name, len(records))
    except OSError as e:
        logger.error("写入 %s 失败：%s", out, e)


def escape_cell(value):
    """表格单元格转义：None/空显示 -，| 转义为 \\|，换行替换为空格。"""
    if value is None:
        return "-"
    text = str(value)
    if text.strip() == "":
        return "-"
    text = text.replace("\r", " ").replace("\n", " ")
    text = text.replace("|", "\\|")
    return text


def render_md(year, records):
    """
    根据年度 JSON 数组生成 README Markdown 文本。
    表头固定，每行为一条记录；空字段显示 -。
    """
    lines = []
    lines.append(f"# 词霸每日一句 - {year}")
    lines.append("")
    lines.append(f"> 最后更新: {now_str_shanghai()} UTC+8")
    lines.append("")
    lines.append("| 编号 | 日期 | 每日一句 | 中文释义 | 中文解读 | 朗读 | 分享图 |")
    lines.append("| :---: | :---: | :--- | :--- | :--- | :---: | :---: |")

    for obj in records:
        sid = escape_cell(obj.get("sid"))
        dateline = escape_cell(obj.get("dateline"))
        content = escape_cell(obj.get("content"))
        note = escape_cell(obj.get("note"))
        translation = escape_cell(obj.get("translation"))

        # 朗读列：tts 存在则生成链接，否则 -
        tts = obj.get("tts")
        tts_cell = f"[TTS]({tts})" if tts else "-"

        # 分享图列：fenxiang_img 存在则生成图片链接，否则 -
        fenxiang_img = obj.get("fenxiang_img")
        if fenxiang_img:
            img_cell = f"[![{escape_cell(dateline)}]({fenxiang_img})]({fenxiang_img})"
        else:
            img_cell = "-"

        lines.append(
            f"| {sid} | {dateline} | {content} | {note} | {translation} | {tts_cell} | {img_cell} |"
        )

    lines.append("")
    return "\n".join(lines)


def rebuild_year_md(year):
    """读取 dsapi-json-{year}.json 并生成 README-dsapi-{year}.md。"""
    src = year_json_path(year)
    if not src.is_file():
        logger.warning("汇总 JSON 不存在，跳过 MD：%s", src)
        return

    try:
        records = json.loads(src.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        logger.error("读取 %s 失败：%s", src, e)
        return

    md_text = render_md(year, records)
    out = year_md_path(year)
    try:
        out.write_text(md_text, encoding="utf-8")
        logger.info("已生成 MD：%s", out.name)
    except OSError as e:
        logger.error("写入 %s 失败：%s", out, e)


def need_rebuild(year, has_new_files):
    """
    判断是否需要重建该年度汇总。
    条件：本年度写入新单日文件，或年度汇总 JSON / README MD 不存在。
    """
    if has_new_files:
        return True
    if not year_json_path(year).is_file():
        return True
    if not year_md_path(year).is_file():
        return True
    return False


def main():
    """脚本入口：固定最近 DEFAULT_DAYS 天，下载归档并按需重建年度汇总 JSON 与 MD。"""
    startdate, enddate = resolve_date_range()
    logger.info("日期范围：%s ~ %s（共 %d 天）", startdate, enddate, DEFAULT_DAYS)

    # 下载归档
    new_files = download_archives(startdate, enddate)

    # 收集涉及的年份
    years = sorted({d.strftime("%Y") for d in iter_dates(startdate, enddate)})

    # 按年份判断并重建汇总 JSON / MD
    for year in years:
        year_has_new = any(str(p.parent.name) == f"dsapi-json-{year}" for p in new_files)
        if need_rebuild(year, year_has_new):
            rebuild_year_json(year)
            rebuild_year_md(year)
        else:
            logger.info("%s 年无需重建汇总", year)

    logger.info("全部完成")


if __name__ == "__main__":
    main()
