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


def get_month_work_days(year: int, month: int, week_days: int = 5) -> dict:
    """调用节假日 API 获取指定月份的工作日/休息日信息，API 不可用时回退到按星期计算"""
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Accept": "application/json",
        "Referer": "http://timor.tech/",
    }
    url = f"http://timor.tech/api/holiday/year/{year}-{month:02d}"
    total_days = calendar.monthrange(year, month)[1]
    work_days, rest_days = 0, 0
    rest_day_list, work_day_list, makeup_day_list = [], [], []

    try:
        resp = requests.get(url, headers=headers, timeout=5)
        holiday_info = resp.json() if resp.status_code == 200 else {}
    except Exception:
        holiday_info = {}

    for day in range(1, total_days + 1):
        date = datetime.date(year, month, day)
        day_str = f"{month:02d}-{day:02d}"
        weekday = date.weekday()  # 0=周一，6=周日
        date_str = date.strftime("%Y-%m-%d")

        if day_str in holiday_info.get("holiday", {}):
            day_info = holiday_info["holiday"][day_str]
            if day_info.get("holiday", False):
                # 节假日
                rest_days += 1
                rest_day_list.append(date_str)
            else:
                # 补班日（原本是周末但需要上班）
                work_days += 1
                work_day_list.append(date_str)
                makeup_day_list.append(date_str)
        else:
            if weekday < week_days:
                work_days += 1
                work_day_list.append(date_str)
            else:
                rest_days += 1
                rest_day_list.append(date_str)

    return {
        "year_month": f"{year}-{month:02d}",
        "total_days": total_days,
        "work_days": work_days,
        "rest_days": rest_days,
        "rest_day_list": rest_day_list,
        "work_day_list": work_day_list,
        "makeup_day_list": makeup_day_list,
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

    info = get_month_work_days(year, month_num, week_days)
    monthly_work_hours = daily_work_hours * info["work_days"]
    hourly_rate = round(salary / monthly_work_hours, 2)

    return {
        "month_str": info["year_month"],
        "total_days": info["total_days"],
        "work_days": info["work_days"],
        "rest_days": info["rest_days"],
        "rest_day_list": info["rest_day_list"],
        "work_day_list": info["work_day_list"],
        "makeup_day_list": info["makeup_day_list"],
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
    parser.add_argument("--month", type=str, default="0", help='月份，格式 2026-05 或 0 表示当月')
    args = parser.parse_args()

    result = calc_hourly_wage(
        salary=args.salary,
        work_time=args.work_time,
        off_time=args.off_time,
        week_days=args.week_days,
        month_in_prompt=args.month,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))