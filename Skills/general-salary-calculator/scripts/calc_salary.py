"""
通用薪资计算器

用法:
    python scripts/calc_salary.py --salary 20000
    python scripts/calc_salary.py --salary 30000 --social-base 25000 --social-rate 0.105 --fund-base 25000 --fund-rate 0.12

输出: JSON（含 summary 字段与 table_md 全年收入明细 Markdown 表格）
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass

MONTHLY_DEDUCTION = 5000  # 每月基本减除费用

# 累计预扣法税率表：(上限, 税率, 速算扣除数)
TAX_BRACKETS: list[tuple[float, float, float]] = [
    (36_000, 0.03, 0),
    (144_000, 0.10, 2_520),
    (300_000, 0.20, 16_920),
    (420_000, 0.25, 31_920),
    (660_000, 0.30, 52_920),
    (960_000, 0.35, 85_920),
    (float("inf"), 0.45, 181_920),
]

BRACKET_UPPERS = [upper for upper, _, _ in TAX_BRACKETS]
BRACKET_RATES = [rate for _, rate, _ in TAX_BRACKETS]


@dataclass
class MonthRow:
    month: int
    pre_tax: float
    social: float
    fund: float
    tax: float
    after_tax: float
    cum_pre_tax: float
    cum_social: float
    cum_fund: float
    cum_tax: float
    tax_formula: str


def calc_cumulative_tax(cumulative_taxable: float) -> float:
    """根据累计应纳税所得额计算累计应预扣预缴税额。"""
    if cumulative_taxable <= 0:
        return 0.0
    for upper, rate, quick in TAX_BRACKETS:
        if cumulative_taxable <= upper:
            return cumulative_taxable * rate - quick
    return 0.0


def fmt_num(value: float) -> str:
    rounded = round(value, 2)
    if rounded == int(rounded):
        return str(int(rounded))
    return f"{rounded:.2f}"


def fmt_rate(rate: float) -> str:
    pct = rate * 100
    if pct == int(pct):
        return f"{int(pct)}\\%"
    return f"{pct:g}\\%"


def build_monthly_tax_formula(prev_cum_taxable: float, curr_cum_taxable: float, tax: float) -> str:
    """
    生成本月个税的 LaTeX 公式：按阶梯拆分当月新增应纳税所得额，
    形如「10500 × 3% = 315」；跨档时为多段相加，最终结果等于当月个税。
    """
    if tax <= 0:
        return "$0 = 0$"

    segments: list[tuple[float, float]] = []
    for idx, upper in enumerate(BRACKET_UPPERS):
        lower = BRACKET_UPPERS[idx - 1] if idx > 0 else 0.0
        seg_start = max(prev_cum_taxable, lower)
        seg_end = min(curr_cum_taxable, upper)
        if seg_end > seg_start:
            segments.append((seg_end - seg_start, BRACKET_RATES[idx]))

    if not segments:
        return f"${fmt_num(tax)} = {fmt_num(tax)}$"

    expr_parts = [f"{fmt_num(amount)} \\times {fmt_rate(rate)}" for amount, rate in segments]
    tax_parts = [fmt_num(round(amount * rate, 2)) for amount, rate in segments]

    if len(segments) == 1:
        return f"${expr_parts[0]} = {fmt_num(tax)}$"

    expr = " + ".join(expr_parts)
    if len(set(tax_parts)) == 1 and len(tax_parts) > 1:
        return f"${expr} = {len(tax_parts)} \\times {tax_parts[0]} = {fmt_num(tax)}$"
    tax_expr = " + ".join(tax_parts)
    return f"${expr} = {tax_expr} = {fmt_num(tax)}$"


def calc_annual_salary(
    salary: float,
    social_base: float,
    social_rate: float,
    fund_base: float,
    fund_rate: float,
) -> tuple[list[MonthRow], float]:
    monthly_social = round(social_base * social_rate, 2)
    monthly_fund = round(fund_base * fund_rate, 2)
    rows: list[MonthRow] = []
    prev_cum_tax = 0.0
    prev_cum_taxable = 0.0
    total_after_tax = 0.0

    for month in range(1, 13):
        cum_pre_tax = round(salary * month, 2)
        cum_social = round(monthly_social * month, 2)
        cum_fund = round(monthly_fund * month, 2)
        cum_deduction = MONTHLY_DEDUCTION * month
        cumulative_taxable = cum_pre_tax - cum_social - cum_fund - cum_deduction
        cum_tax = round(calc_cumulative_tax(cumulative_taxable), 2)
        tax = round(cum_tax - prev_cum_tax, 2)
        after_tax = round(salary - monthly_social - monthly_fund - tax, 2)
        tax_formula = build_monthly_tax_formula(prev_cum_taxable, cumulative_taxable, tax)

        rows.append(
            MonthRow(
                month=month,
                pre_tax=salary,
                social=monthly_social,
                fund=monthly_fund,
                tax=tax,
                after_tax=after_tax,
                cum_pre_tax=cum_pre_tax,
                cum_social=cum_social,
                cum_fund=cum_fund,
                cum_tax=cum_tax,
                tax_formula=tax_formula,
            )
        )
        prev_cum_tax = cum_tax
        prev_cum_taxable = cumulative_taxable
        total_after_tax += after_tax

    avg_after_tax = round(total_after_tax / 12, 2)
    return rows, avg_after_tax


def fmt_money(value: float) -> str:
    return f"{value:,.2f}"


def build_table_md(rows: list[MonthRow]) -> str:
    header = (
        "| 月份 | 当月税前工资 | 当月社保 | 当月公积金 | 当月个税 | 当月税后工资 | "
        "累积税前工资 | 累积社保 | 累积公积金 | 累积个税 | 当月个税比例 |"
    )
    sep = "|---|" + "|".join(["---:"] * 10) + "|"
    lines = [header, sep]
    for row in rows:
        lines.append(
            f"| {row.month} | {fmt_money(row.pre_tax)} | {fmt_money(row.social)} | "
            f"{fmt_money(row.fund)} | {fmt_money(row.tax)} | {fmt_money(row.after_tax)} | "
            f"{fmt_money(row.cum_pre_tax)} | {fmt_money(row.cum_social)} | "
            f"{fmt_money(row.cum_fund)} | {fmt_money(row.cum_tax)} | {row.tax_formula} |"
        )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="通用薪资计算器")
    parser.add_argument("--salary", type=float, required=True, help="税前月工资（元）")
    parser.add_argument(
        "--social-base",
        type=float,
        default=None,
        help="社保基数（元），默认与税前工资相同（足额）",
    )
    parser.add_argument(
        "--social-rate",
        type=float,
        default=0.105,
        help="社保个人缴纳比例，默认 0.105（10.5%%）",
    )
    parser.add_argument(
        "--fund-base",
        type=float,
        default=None,
        help="公积金基数（元），默认与税前工资相同（足额）",
    )
    parser.add_argument(
        "--fund-rate",
        type=float,
        default=0.12,
        help="公积金个人缴纳比例，默认 0.12（12%%）",
    )
    args = parser.parse_args()

    salary = args.salary
    social_base = args.social_base if args.social_base is not None else salary
    fund_base = args.fund_base if args.fund_base is not None else salary

    rows, avg_after_tax = calc_annual_salary(
        salary=salary,
        social_base=social_base,
        social_rate=args.social_rate,
        fund_base=fund_base,
        fund_rate=args.fund_rate,
    )
    monthly_social = rows[0].social
    monthly_fund = rows[0].fund

    result = {
        "pre_tax_salary": salary,
        "avg_after_tax": avg_after_tax,
        "monthly_social": monthly_social,
        "monthly_fund": monthly_fund,
        "social_base": social_base,
        "social_rate": args.social_rate,
        "fund_base": fund_base,
        "fund_rate": args.fund_rate,
        "table_md": build_table_md(rows),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
