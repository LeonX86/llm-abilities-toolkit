"""
时薪计算器脚本

用法:
    python scripts/calc.py --salary 15000 --work-time 9 --off-time 18 --week-days 5 --month 0
    python scripts/calc.py --salary 20000 --work-time 9 --off-time 21 --week-days 6 --month 2026-05

输出: JSON 格式的计算结果，打印到 stdout
"""

import argparse
import calendar
import datetime
import json

import requests

WEEKDAY_CN = ["一", "二", "三", "四", "五", "六", "日"]
HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept": "application/json",
    "Referer": "http://timor.tech/",
}


def get_month_data(year: int, month: int, week_days: int = 5) -> dict:
    """单次 API 调用，返回当月工作日统计、日期列表及日历 Markdown 表格"""
    url = f"http://timor.tech/api/holiday/year/{year}-{month:02d}"
    api_ok = True
    try:
        resp = requests.get(url, headers=HEADERS, timeout=5)
        holiday_info = resp.json().get("holiday", {}) if resp.status_code == 200 else {}
        if not holiday_info and resp.status_code != 200:
            api_ok = False
    except Exception:
        holiday_info = {}
        api_ok = False

    total_days = calendar.monthrange(year, month)[1]
    work_days, rest_days = 0, 0
    rest_day_list, makeup_day_list = [], []

    # {day: "工作" | "休息" | "**补班**"}
    day_status: dict[int, str] = {}

    for day in range(1, total_days + 1):
        date = datetime.date(year, month, day)
        day_key = f"{month:02d}-{day:02d}"
        weekday = date.weekday()
        date_str = date.strftime("%Y-%m-%d")

        if day_key in holiday_info:
            info = holiday_info[day_key]
            if info.get("holiday", False):
                rest_days += 1
                rest_day_list.append(date_str)
                day_status[day] = "休息"
            else:
                work_days += 1
                makeup_day_list.append(date_str)
                day_status[day] = "**补班**"
        else:
            if weekday < week_days:
                work_days += 1
                day_status[day] = "工作"
            else:
                rest_days += 1
                rest_day_list.append(date_str)
                day_status[day] = "休息"

    # 生成日历 Markdown 表格
    calendar_lines = []
    if not api_ok:
        calendar_lines.append("> 节假日 API 不可用，以下日历按常规星期推算，节假日与补班可能不准确。\n")

    header = f"| {year} 年 {month:02d} 月 | " + " | ".join(WEEKDAY_CN) + " |"
    calendar_lines.append(header)
    calendar_lines.append("|---|---|---|---|---|---|---|---|")

    first_weekday = datetime.date(year, month, 1).weekday()
    day = 1
    week_num = 1
    while day <= total_days:
        cells = []
        for col in range(7):
            if week_num == 1 and col < first_weekday:
                cells.append("   ")
            elif day > total_days:
                cells.append("   ")
            else:
                cells.append(f"{day:02d} {day_status[day]}")
                day += 1
        calendar_lines.append(f"| 第{week_num}周 | " + " | ".join(cells) + " |")
        week_num += 1

    return {
        "year_month": f"{year}-{month:02d}",
        "total_days": total_days,
        "work_days": work_days,
        "rest_days": rest_days,
        "rest_day_list": rest_day_list,
        "makeup_day_list": makeup_day_list,
        "calendar_md": "\n".join(calendar_lines),
    }


def calc_hourly_wage(
    salary: float,
    work_time: float,
    off_time: float,
    week_days: int,
    month_in_prompt: str,
) -> dict:
    daily_work_hours = off_time - work_time
    if daily_work_hours <= 0:
        daily_work_hours += 24

    if month_in_prompt == "0":
        now = datetime.datetime.now()
        year, month_num = now.year, now.month
    else:
        year, month_num = map(int, month_in_prompt.split("-"))

    info = get_month_data(year, month_num, week_days)
    monthly_work_hours = daily_work_hours * info["work_days"]
    hourly_rate = round(salary / monthly_work_hours, 2)

    return {
        "month_str": info["year_month"],
        "total_days": info["total_days"],
        "work_days": info["work_days"],
        "rest_days": info["rest_days"],
        "rest_day_list": info["rest_day_list"],
        "makeup_day_list": info["makeup_day_list"],
        "calendar_md": info["calendar_md"],
        "daily_work_hours": daily_work_hours,
        "monthly_work_hours": monthly_work_hours,
        "hourly_rate": hourly_rate,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="时薪计算器")
    parser.add_argument("--salary", type=float, required=True, help="月薪金额（元）")
    parser.add_argument("--work-time", type=float, required=True, help="上班时刻（24小时制，如 9）")
    parser.add_argument("--off-time", type=float, required=True, help="下班时刻（24小时制，如 18）")
    parser.add_argument("--week-days", type=int, default=5, help="每周工作天数，默认 5")
    parser.add_argument("--month", type=str, default="0", help="月份，格式 2026-05 或 0 表示当月")
    args = parser.parse_args()

    result = calc_hourly_wage(
        salary=args.salary,
        work_time=args.work_time,
        off_time=args.off_time,
        week_days=args.week_days,
        month_in_prompt=args.month,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
