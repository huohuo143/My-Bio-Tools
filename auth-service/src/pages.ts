import { escapeHTML } from "./crypto.ts";
import type { DeviceRow, UserRow } from "./types.ts";

const baseStyles = `
  :root { color-scheme: light dark; font-family: -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; }
  body { margin:0; min-height:100vh; display:grid; place-items:center; background:#f3f7f6; color:#17332e; }
  main { width:min(880px,calc(100% - 32px)); box-sizing:border-box; background:white; border:1px solid #dce9e6; border-radius:18px; padding:32px; box-shadow:0 18px 50px rgba(20,70,60,.12); }
  h1 { margin-top:0; } label { display:block; margin:14px 0 6px; font-weight:600; }
  input,textarea,select { width:100%; box-sizing:border-box; padding:10px 12px; border:1px solid #b8cbc7; border-radius:9px; font:inherit; background:white; color:#17332e; }
  button { margin-top:18px; border:0; border-radius:9px; padding:10px 16px; background:#147d68; color:white; font-weight:700; cursor:pointer; }
  .muted { color:#627b76; } .error { color:#b42318; } a { color:#0b6f5b; }
  .authorization-box { margin-top:14px; padding:14px; border:1px solid #dce9e6; border-radius:12px; background:#f7faf9; }
  .period-options { display:flex; flex-wrap:wrap; gap:9px; margin-top:8px; }
  .period-option { display:inline-flex; align-items:center; gap:6px; margin:0; padding:8px 11px; border:1px solid #b8cbc7; border-radius:999px; font-weight:600; background:white; }
  .period-option input { width:auto; margin:0; }
  .custom-date { max-width:260px; }
  @media (prefers-color-scheme:dark) { body{background:#10201d;color:#edf7f5} main{background:#182d29;border-color:#31514b} input,textarea,select{background:#10201d;color:#edf7f5;border-color:#4d6c66} .authorization-box{background:#10201d;border-color:#31514b}.period-option{background:#182d29;border-color:#4d6c66}.muted{color:#a9c1bc} }
`;

export function page(title: string, body: string, status = 200, scriptNonce?: string): Response {
  return new Response(`<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>${escapeHTML(title)}</title><style>${baseStyles}</style></head><body><main>${body}</main></body></html>`, {
    status,
    headers: {
      "content-type": "text/html; charset=utf-8",
      "cache-control": "no-store",
      "content-security-policy": `default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; connect-src 'self'; ${scriptNonce ? `script-src 'nonce-${scriptNonce}';` : ""} frame-ancestors 'none'; base-uri 'none'`,
      "x-content-type-options": "nosniff",
      "referrer-policy": "no-referrer",
    },
  });
}

export function resetPasswordForm(token: string, message = ""): Response {
  return page("重置密码", `<h1>重置密码</h1>${message}<form method="post" action="/reset-password"><input type="hidden" name="token" value="${escapeHTML(token)}"><label>新密码</label><input name="password" type="password" minlength="8" maxlength="128" required autocomplete="new-password"><label>确认新密码</label><input name="confirm" type="password" minlength="8" maxlength="128" required autocomplete="new-password"><button type="submit">确认重置</button></form><p class="muted">密码至少 8 个字符。重置后所有已登录设备都会退出。</p>`);
}

function authorizationText(user: UserRow): string {
  if (user.status !== "active") return "当前未授权";
  if (user.authorization_expires_at === null) return "当前授权：永久";
  const date = new Date(user.authorization_expires_at * 1000).toLocaleString("zh-CN", {
    timeZone: "Asia/Shanghai", year: "numeric", month: "2-digit", day: "2-digit",
    hour: "2-digit", minute: "2-digit", hour12: false,
  });
  return user.authorization_expires_at <= Math.floor(Date.now() / 1000)
    ? `授权已到期：${date}`
    : `当前授权至：${date}`;
}

export function adminDashboard(
  users: UserRow[] = [],
  counts: Record<string, number> = {},
  selectedStatus = "pending",
  query = "",
  devicesByUser: Record<string, DeviceRow[]> = {},
  message = "",
): Response {
  const total = Object.values(counts).reduce((sum, value) => sum + Number(value || 0), 0);
  const filters = [
    ["pending", "待审核", counts.pending ?? 0],
    ["active", "正常", counts.active ?? 0],
    ["expired", "已到期", counts.expired ?? 0],
    ["rejected", "已拒绝", counts.rejected ?? 0],
    ["suspended", "已停用", counts.suspended ?? 0],
    ["all", "全部", total],
  ].map(([value, label, count]) => {
    const href = `/admin/?status=${encodeURIComponent(String(value))}&q=${encodeURIComponent(query)}`;
    const selected = value === selectedStatus ? "background:#0c5f50" : "";
    return `<a href="${href}" style="display:inline-block;padding:9px 13px;border-radius:9px;background:#147d68;color:white;text-decoration:none;${selected}">${label} (${count})</a>`;
  }).join(" ");

  const rows = users.map((user) => {
    const id = escapeHTML(user.id);
    const email = escapeHTML(user.email);
    const status = escapeHTML(user.status);
    const devices = devicesByUser[user.id];
    const deviceSection = devices ? `<div style="margin:12px 0;padding:12px;background:#f3f7f6;border-radius:9px"><strong>已绑定设备</strong>${devices.map((device) => `
      <p>${escapeHTML(device.device_name)} · ${escapeHTML(device.platform)} · ${escapeHTML(device.app_version)}${device.revoked_at ? " · 已解绑" : `
      <form method="post" action="/admin/action" style="display:inline"><input type="hidden" name="action" value="revoke_device"><input type="hidden" name="userId" value="${id}"><input type="hidden" name="deviceId" value="${escapeHTML(device.id)}"><button type="submit">解绑</button></form>`}</p>`).join("") || "<p>无设备</p>"}</div>` : "";
    const deviceLink = `/admin/?status=${encodeURIComponent(selectedStatus)}&q=${encodeURIComponent(query)}&devices=${encodeURIComponent(user.id)}`;
    return `<section style="border-top:1px solid #dce9e6;padding:18px 0">
      <h3>${escapeHTML(user.real_name)} <small>${status}</small></h3>
      <p>${email} · ${escapeHTML(user.lab_role)}</p><p>${escapeHTML(user.application_note)}</p>
      <p class="muted">申请时间：${new Date(user.created_at * 1000).toLocaleString("zh-CN", { timeZone: "Asia/Shanghai" })} · 邮箱：${user.email_verified_at ? "已验证" : "未验证"}</p>
      <p><strong>${escapeHTML(authorizationText(user))}</strong></p>
      <form method="post" action="/admin/action"><input type="hidden" name="action" value="set_status"><input type="hidden" name="userId" value="${id}">
        <div class="authorization-box"><strong>授权期限</strong><div class="period-options">
          <label class="period-option"><input type="radio" name="authorizationPeriod" value="1_month">1月</label>
          <label class="period-option"><input type="radio" name="authorizationPeriod" value="6_months">6月</label>
          <label class="period-option"><input type="radio" name="authorizationPeriod" value="1_year" checked>1年</label>
          <label class="period-option"><input type="radio" name="authorizationPeriod" value="2_years">2年</label>
          <label class="period-option"><input type="radio" name="authorizationPeriod" value="permanent">永久</label>
          <label class="period-option"><input type="radio" name="authorizationPeriod" value="custom">自定义时间</label>
        </div><label>自定义到期日期（选“自定义时间”时填写）</label><input class="custom-date" name="customExpiresOn" type="date">
        <p class="muted">新授权从批准时起计算；离线凭证最长 7 天，且不会超过账号授权到期时间。</p></div>
        <input name="reason" placeholder="拒绝或停用原因（可留空）"><button name="status" value="active" type="submit">批准/更新授权</button> <button name="status" value="rejected" type="submit">拒绝</button> <button name="status" value="suspended" type="submit">停用</button>
      </form>
      <p><a href="${deviceLink}">查看设备</a></p>${deviceSection}
      <form method="post" action="/admin/action"><input type="hidden" name="action" value="force_logout"><input type="hidden" name="userId" value="${id}"><button type="submit">强制退出全部设备</button></form>
      <form method="post" action="/admin/action"><input type="hidden" name="action" value="send_password_reset"><input type="hidden" name="userId" value="${id}"><button type="submit">发送密码重置邮件</button></form>
      <details style="margin-top:18px"><summary>永久删除账号</summary><form method="post" action="/admin/action"><input type="hidden" name="action" value="delete_user"><input type="hidden" name="userId" value="${id}"><label>再次输入邮箱</label><input name="email" type="email" required><label>输入 DELETE</label><input name="confirmation" required><button type="submit">确认永久删除</button></form></details>
    </section>`;
  }).join("") || "<p>没有匹配账号。</p>";

  return page("My Bio Tools 账号管理", `
<h1>My Bio Tools 账号管理</h1>
<p class="muted">审核注册、设置授权期限、停用账号、强制退出与解绑设备。所有操作都会记录审计日志。</p>
${message ? `<p style="padding:10px;border-radius:9px;background:#e8f5f1">${escapeHTML(message)}</p>` : ""}
<nav style="display:flex;gap:8px;flex-wrap:wrap">${filters}</nav>
<form method="get" action="/admin/"><input type="hidden" name="status" value="${escapeHTML(selectedStatus)}"><label>搜索邮箱或姓名</label><input name="q" value="${escapeHTML(query)}"><button type="submit">搜索</button></form>
<div>${rows}</div>`);
}
