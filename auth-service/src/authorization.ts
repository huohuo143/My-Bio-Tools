export type AuthorizationPeriod =
  | "1_month"
  | "6_months"
  | "1_year"
  | "2_years"
  | "permanent"
  | "custom";

export interface AuthorizationSelection {
  period: AuthorizationPeriod;
  expiresAt: number | null;
  label: string;
}

export class AuthorizationPeriodError extends Error {}

const SHANGHAI_OFFSET_SECONDS = 8 * 60 * 60;

function addCalendarMonths(now: number, months: number): number {
  const shanghai = new Date((now + SHANGHAI_OFFSET_SECONDS) * 1000);
  const sourceYear = shanghai.getUTCFullYear();
  const sourceMonth = shanghai.getUTCMonth();
  const targetMonthIndex = sourceYear * 12 + sourceMonth + months;
  const targetYear = Math.floor(targetMonthIndex / 12);
  const targetMonth = targetMonthIndex % 12;
  const lastDay = new Date(Date.UTC(targetYear, targetMonth + 1, 0)).getUTCDate();
  const targetDay = Math.min(shanghai.getUTCDate(), lastDay);
  return Math.floor(Date.UTC(
    targetYear,
    targetMonth,
    targetDay,
    shanghai.getUTCHours(),
    shanghai.getUTCMinutes(),
    shanghai.getUTCSeconds(),
  ) / 1000) - SHANGHAI_OFFSET_SECONDS;
}

function customDateExpiration(value: string, now: number): number {
  const matched = /^(\d{4})-(\d{2})-(\d{2})$/u.exec(value);
  if (!matched) throw new AuthorizationPeriodError("请选择有效的自定义到期日期。");
  const year = Number(matched[1]);
  const month = Number(matched[2]);
  const day = Number(matched[3]);
  const parsed = new Date(Date.UTC(year, month - 1, day));
  if (
    parsed.getUTCFullYear() !== year ||
    parsed.getUTCMonth() !== month - 1 ||
    parsed.getUTCDate() !== day
  ) {
    throw new AuthorizationPeriodError("请选择有效的自定义到期日期。");
  }
  const expiresAt = Math.floor(Date.UTC(year, month - 1, day, 23, 59, 59) / 1000)
    - SHANGHAI_OFFSET_SECONDS;
  if (expiresAt <= now) throw new AuthorizationPeriodError("自定义到期日期必须晚于当前时间。");
  return expiresAt;
}

export function resolveAuthorizationPeriod(
  period: string | undefined,
  customExpiresOn: string | undefined,
  now: number,
): AuthorizationSelection {
  switch (period) {
  case "1_month":
    return { period, expiresAt: addCalendarMonths(now, 1), label: "1 个月" };
  case "6_months":
    return { period, expiresAt: addCalendarMonths(now, 6), label: "6 个月" };
  case "1_year":
    return { period, expiresAt: addCalendarMonths(now, 12), label: "1 年" };
  case "2_years":
    return { period, expiresAt: addCalendarMonths(now, 24), label: "2 年" };
  case "permanent":
    return { period, expiresAt: null, label: "永久" };
  case "custom": {
    const date = (customExpiresOn ?? "").trim();
    return { period, expiresAt: customDateExpiration(date, now), label: `自定义至 ${date}` };
  }
  default:
    throw new AuthorizationPeriodError("请选择授权期限：1月、6月、1年、2年、永久或自定义时间。");
  }
}

export function authorizationIsExpired(expiresAt: number | null, now: number): boolean {
  return expiresAt !== null && expiresAt <= now;
}
