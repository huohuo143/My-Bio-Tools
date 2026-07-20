import { escapeHTML } from "./crypto.ts";
import type { Env, UserRow } from "./types.ts";

async function send(env: Env, to: string, subject: string, text: string, html: string): Promise<void> {
  if (env.ENVIRONMENT === "test" && env.EMAIL_TEST_SENDER) {
    await env.EMAIL_TEST_SENDER.send({ to, from: env.EMAIL_FROM, subject, text, html });
    return;
  }
  if (!env.RESEND_API_KEY) throw new Error("Resend API key is not configured");

  const response = await fetch("https://api.resend.com/emails", {
    method: "POST",
    headers: {
      authorization: `Bearer ${env.RESEND_API_KEY}`,
      "content-type": "application/json",
      "user-agent": "MyBioToolsAuth/1.9.5",
    },
    body: JSON.stringify({
      from: env.EMAIL_FROM,
      to: [to],
      subject,
      text,
      html,
    }),
  });
  if (!response.ok) {
    throw new Error(`Resend email request failed with HTTP ${response.status}`);
  }
}

export async function sendVerificationEmail(env: Env, user: UserRow, token: string): Promise<void> {
  const link = `${env.APP_ORIGIN}/verify-email?token=${encodeURIComponent(token)}`;
  await send(
    env,
    user.email,
    "验证您的 My Bio Tools 邮箱",
    `您好，${user.real_name}。请在 24 小时内打开以下链接完成邮箱验证：\n${link}`,
    `<p>您好，${escapeHTML(user.real_name)}。</p><p>请在 24 小时内完成邮箱验证：</p><p><a href="${link}">验证邮箱</a></p>`,
  );
}

export async function sendResetEmail(env: Env, user: UserRow, token: string): Promise<void> {
  const link = `${env.APP_ORIGIN}/reset-password?token=${encodeURIComponent(token)}`;
  await send(
    env,
    user.email,
    "重置 My Bio Tools 密码",
    `请在 30 分钟内打开以下链接重置密码：\n${link}\n如果并非您本人操作，请忽略此邮件。`,
    `<p>请在 30 分钟内重置密码：</p><p><a href="${link}">重置密码</a></p><p>如果并非您本人操作，请忽略此邮件。</p>`,
  );
}

export async function sendAdminRegistrationNotice(env: Env, user: UserRow): Promise<void> {
  if (!env.ADMIN_NOTIFICATION_EMAIL) return;
  const link = `${env.APP_ORIGIN}/admin/`;
  await send(
    env,
    env.ADMIN_NOTIFICATION_EMAIL,
    `My Bio Tools 新申请：${user.real_name}`,
    `${user.real_name}（${user.email}）已验证邮箱，等待审核。\n${link}`,
    `<p><strong>${escapeHTML(user.real_name)}</strong>（${escapeHTML(user.email)}）已验证邮箱，等待审核。</p><p><a href="${link}">打开管理后台</a></p>`,
  );
}

export async function sendReviewNotice(env: Env, user: UserRow, status: string, reason: string, previousStatus?: string): Promise<void> {
  const label = status === "active"
    ? (previousStatus === "active" ? "授权已更新" : previousStatus === "suspended" || previousStatus === "rejected" ? "已恢复" : "已批准")
    : status === "suspended" ? "已停用" : "未通过";
  const reasonText = reason ? `\n说明：${reason}` : "";
  const authorizationText = status !== "active" ? "" : user.authorization_expires_at === null
    ? "\n授权期限：永久"
    : `\n授权到期：${new Date(user.authorization_expires_at * 1000).toLocaleString("zh-CN", {
      timeZone: "Asia/Shanghai", hour12: false,
    })}`;
  const authorizationHTML = authorizationText
    ? `<p>${escapeHTML(authorizationText.trim())}</p>`
    : "";
  await send(
    env,
    user.email,
    `My Bio Tools 账号${label}`,
    `您的 My Bio Tools 账号${label}。${authorizationText}${reasonText}`,
    `<p>您的 My Bio Tools 账号<strong>${label}</strong>。</p>${authorizationHTML}${reason ? `<p>说明：${escapeHTML(reason)}</p>` : ""}`,
  );
}
