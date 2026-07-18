import { requireAdmin } from "./access.ts";
import {
  hashPassword, isValidEmail, normalizeEmail, randomToken, sha256, signJWT,
  validateSigningConfiguration, verifyPassword,
} from "./crypto.ts";
import { sendAdminRegistrationNotice, sendResetEmail, sendReviewNotice, sendVerificationEmail } from "./email.ts";
import { bearerToken, enforceRateLimit, error, json, readJson, sourceHash } from "./http.ts";
import { adminDashboard, page, resetPasswordForm } from "./pages.ts";
import { Repository } from "./repository.ts";
import { authenticateAccess, installationHash, issueTokens, validatedOmicsDatabaseKey } from "./tokens.ts";
import type { Env, UpdateManifestClaims, UserRow, UserStatus } from "./types.ts";

const EMAIL_VERIFY_SECONDS = 24 * 60 * 60;
const PASSWORD_RESET_SECONDS = 30 * 60;

const nowSeconds = (): number => Math.floor(Date.now() / 1000);

function publicUser(user: UserRow): Record<string, unknown> {
  return {
    id: user.id, email: user.email, realName: user.real_name, labRole: user.lab_role,
    applicationNote: user.application_note, status: user.status,
    emailVerifiedAt: user.email_verified_at, reviewReason: user.review_reason, createdAt: user.created_at,
  };
}

function validatePassword(password: string): string | null {
  if (password.length < 8) return "密码至少需要 8 个字符。";
  if (password.length > 128) return "密码不能超过 128 个字符。";
  return null;
}

async function writeAudit(
  repository: Repository,
  request: Request,
  env: Env,
  input: Omit<Parameters<Repository["audit"]>[0], "sourceHash" | "now">,
): Promise<void> {
  await repository.audit({ ...input, sourceHash: await sourceHash(request, env), now: nowSeconds() });
}

async function handleRegister(request: Request, env: Env): Promise<Response> {
  const body = await readJson<{
    email?: string; realName?: string; labRole?: string; applicationNote?: string; password?: string;
  }>(request);
  const email = normalizeEmail(body.email ?? "");
  await enforceRateLimit(request, env, "register", email);
  const realName = (body.realName ?? "").trim();
  const labRole = (body.labRole ?? "").trim();
  const applicationNote = (body.applicationNote ?? "").trim();
  const password = body.password ?? "";
  const passwordError = validatePassword(password);
  if (!isValidEmail(email)) return error("INVALID_EMAIL", "请输入有效邮箱。", 400);
  if (realName.length < 2 || realName.length > 80) return error("INVALID_REAL_NAME", "真实姓名长度不符合要求。", 400);
  if (labRole.length < 2 || labRole.length > 80) return error("INVALID_LAB_ROLE", "请填写课题组身份。", 400);
  if (applicationNote.length > 500) return error("APPLICATION_NOTE_TOO_LONG", "申请说明不能超过 500 字。", 400);
  if (passwordError) return error("WEAK_PASSWORD", passwordError, 400);

  const repository = new Repository(env.DB);
  if (await repository.getUserByEmail(email)) {
    return json({ message: "如果该邮箱尚未注册，系统将发送验证邮件。" }, 202);
  }
  const now = nowSeconds();
  const salt = randomToken(16);
  const user: UserRow = {
    id: crypto.randomUUID(), email, real_name: realName, lab_role: labRole,
    application_note: applicationNote, password_hash: await hashPassword(password, salt, env.PASSWORD_PEPPER),
    password_salt: salt, status: "unverified", email_verified_at: null, reviewed_at: null,
    reviewed_by: null, review_reason: null, failed_attempts: 0, locked_until: null,
    created_at: now, updated_at: now,
  };
  const verificationToken = randomToken();
  await repository.createUser(user);
  await repository.replaceOneTimeToken(user.id, "verify_email", await sha256(verificationToken), now, now + EMAIL_VERIFY_SECONDS);
  await writeAudit(repository, request, env, {
    actorType: "user", actorId: user.id, action: "register", targetType: "user", targetId: user.id,
  });
  try {
    await sendVerificationEmail(env, user, verificationToken);
  } catch (emailError) {
    console.error("verification_email_failed", { userId: user.id, name: (emailError as Error).name });
    return error("EMAIL_SEND_FAILED", "账号已建立，但验证邮件发送失败，请稍后重发。", 503);
  }
  return json({ message: "验证邮件已发送，请在 24 小时内完成验证。", status: "unverified" }, 201);
}

async function handleResendVerification(request: Request, env: Env): Promise<Response> {
  const body = await readJson<{ email?: string }>(request);
  const email = normalizeEmail(body.email ?? "");
  await enforceRateLimit(request, env, "resend-verification", email);
  const repository = new Repository(env.DB);
  const user = await repository.getUserByEmail(email);
  if (user?.status === "unverified") {
    const now = nowSeconds();
    const token = randomToken();
    await repository.replaceOneTimeToken(user.id, "verify_email", await sha256(token), now, now + EMAIL_VERIFY_SECONDS);
    await sendVerificationEmail(env, user, token);
  }
  return json({ message: "如果账号存在且尚未验证，系统已重发邮件。" }, 202);
}

async function handleVerifyEmail(url: URL, request: Request, env: Env): Promise<Response> {
  const tokenValue = url.searchParams.get("token") ?? "";
  if (!tokenValue) return page("验证失败", "<h1>链接无效</h1><p>缺少验证令牌。</p>", 400);
  const repository = new Repository(env.DB);
  const now = nowSeconds();
  const token = await repository.getValidToken(await sha256(tokenValue), "verify_email", now);
  if (!token || !(await repository.markEmailVerified(token, now))) {
    return page("验证失败", "<h1>链接已失效</h1><p>请返回 APP 重新发送验证邮件。</p>", 400);
  }
  const user = await repository.getUserById(token.user_id);
  if (user) {
    await writeAudit(repository, request, env, {
      actorType: "user", actorId: user.id, action: "verify_email", targetType: "user", targetId: user.id,
    });
    try { await sendAdminRegistrationNotice(env, user); }
    catch (emailError) { console.error("admin_registration_notice_failed", { userId: user.id, name: (emailError as Error).name }); }
  }
  return page("验证成功", "<h1>邮箱已验证</h1><p>您的申请已进入管理员审核。审核通过后即可在 APP 中登录。</p>");
}

async function handleLogin(request: Request, env: Env): Promise<Response> {
  const body = await readJson<{
    email?: string; password?: string; installationId?: string; platform?: string;
    deviceName?: string; appVersion?: string;
  }>(request);
  const email = normalizeEmail(body.email ?? "");
  await enforceRateLimit(request, env, "login", email);
  const installationId = (body.installationId ?? "").trim();
  const platform = body.platform;
  if (!installationId || installationId.length > 200 || (platform !== "macos" && platform !== "windows")) {
    return error("INVALID_DEVICE", "设备信息无效。", 400);
  }
  const repository = new Repository(env.DB);
  const user = await repository.getUserByEmail(email);
  const now = nowSeconds();
  if (!user) {
    await hashPassword(body.password ?? "", randomToken(16), env.PASSWORD_PEPPER);
    return error("INVALID_CREDENTIALS", "邮箱或密码错误。", 401);
  }
  if (user.locked_until && user.locked_until > now) return error("LOGIN_COOLDOWN", "登录失败次数过多，请稍后重试。", 429);
  if (!(await verifyPassword(body.password ?? "", user.password_salt, user.password_hash, env.PASSWORD_PEPPER))) {
    await repository.recordFailedLogin(user, now);
    return error("INVALID_CREDENTIALS", "邮箱或密码错误。", 401);
  }
  await repository.clearFailedLogin(user.id, now);
  const states: Partial<Record<UserStatus, [string, string, number]>> = {
    unverified: ["EMAIL_UNVERIFIED", "请先完成邮箱验证。", 403],
    pending: ["PENDING_REVIEW", "申请正在等待管理员审核。", 403],
    rejected: ["ACCOUNT_REJECTED", user.review_reason || "申请未通过审核。", 403],
    suspended: ["ACCOUNT_SUSPENDED", user.review_reason || "账号已停用。", 403],
    deleted: ["ACCOUNT_DELETED", "账号不可用。", 403],
  };
  if (user.status !== "active") {
    const [code, message, status] = states[user.status] ?? ["ACCOUNT_UNAVAILABLE", "账号不可用。", 403];
    return error(code, message, status);
  }
  // Validate the runtime key format and key pair before mutating devices or
  // sessions. This catches cross-runtime JWK incompatibilities during login
  // without leaving orphaned authorization records.
  await validateSigningConfiguration(env.LICENSE_PRIVATE_JWK, env.LICENSE_PUBLIC_JWK);
  const device = await repository.bindDevice({
    userId: user.id, installationHash: await installationHash(installationId), platform,
    deviceName: (body.deviceName ?? platform).trim().slice(0, 120),
    appVersion: (body.appVersion ?? "unknown").trim().slice(0, 40), now,
  });
  if (!device) return error("DEVICE_LIMIT_REACHED", "该账号已绑定 2 台设备，请先解绑旧设备。", 409);
  const refreshToken = randomToken();
  const session = await repository.createSession(user.id, device.id, await sha256(refreshToken), now);
  await writeAudit(repository, request, env, {
    actorType: "user", actorId: user.id, action: "login", targetType: "device", targetId: device.id,
    metadata: { platform, appVersion: body.appVersion ?? "unknown" },
  });
  return json({ user: publicUser(user), ...(await issueTokens(env, session, device, refreshToken, now)) });
}

async function handleRefresh(request: Request, env: Env): Promise<Response> {
  const body = await readJson<{ refreshToken?: string; installationId?: string }>(request);
  const refreshToken = body.refreshToken ?? "";
  const repository = new Repository(env.DB);
  const now = nowSeconds();
  const oldHash = await sha256(refreshToken);
  const session = await repository.getSessionByRefresh(oldHash, now);
  if (!session) return error("SESSION_EXPIRED", "登录已过期，请重新登录。", 401);
  const user = await repository.getUserById(session.user_id);
  const deviceHash = await installationHash(body.installationId ?? "");
  const device = (await repository.listDevices(session.user_id)).find((entry) => entry.id === session.device_id);
  if (!user || user.status !== "active" || !device || device.revoked_at !== null || device.installation_hash !== deviceHash) {
    await repository.revokeSession(session.id, now);
    return error("AUTHORIZATION_REVOKED", "账号或设备授权已撤销。", 403);
  }
  const nextRefreshToken = randomToken();
  const nextHash = await sha256(nextRefreshToken);
  if (!(await repository.rotateSession(session.id, oldHash, nextHash, now))) {
    return error("SESSION_ROTATION_FAILED", "会话刷新失败，请重新登录。", 401);
  }
  const rotatedSession = { ...session, refresh_hash: nextHash, last_seen_at: now };
  return json({ user: publicUser(user), ...(await issueTokens(env, rotatedSession, device, nextRefreshToken, now)) });
}

async function requireUser(request: Request, env: Env) {
  const token = bearerToken(request);
  if (!token) throw new Response("Missing access token", { status: 401 });
  try { return await authenticateAccess(token, env); }
  catch { throw new Response("Authorization revoked", { status: 403 }); }
}

async function handleRenewLicense(request: Request, env: Env): Promise<Response> {
  const context = await requireUser(request, env);
  const tokens = await issueTokens(env, context.session, context.device, "", nowSeconds());
  delete tokens.refreshToken;
  delete tokens.refreshExpiresAt;
  return json(tokens);
}

async function handleMe(request: Request, env: Env): Promise<Response> {
  const context = await requireUser(request, env);
  return json({ user: publicUser(context.user), serverTime: nowSeconds() });
}

async function handleDevices(request: Request, env: Env): Promise<Response> {
  const context = await requireUser(request, env);
  const devices = await new Repository(env.DB).listDevices(context.user.id);
  return json({ devices: devices.map((device) => ({
    id: device.id, platform: device.platform, deviceName: device.device_name,
    appVersion: device.app_version, firstSeenAt: device.first_seen_at,
    lastSeenAt: device.last_seen_at, revokedAt: device.revoked_at, current: device.id === context.device.id,
  })) });
}

async function handleDeleteDevice(request: Request, env: Env, deviceId: string): Promise<Response> {
  const context = await requireUser(request, env);
  const repository = new Repository(env.DB);
  if (!(await repository.revokeDevice(context.user.id, deviceId, nowSeconds()))) return error("DEVICE_NOT_FOUND", "未找到可解绑设备。", 404);
  await writeAudit(repository, request, env, {
    actorType: "user", actorId: context.user.id, action: "revoke_device", targetType: "device", targetId: deviceId,
  });
  return json({ message: "设备已解绑。", currentDeviceRevoked: deviceId === context.device.id });
}

interface UpdateManifestConfiguration {
  platform: "macos-arm64";
  bundleIdentifier: "top.aizs.my-bio-tools";
  appVersion: string;
  build: number;
  minimumSystemVersion: string;
  size: number;
  sha256: string;
  r2Key: string;
  releaseNotes: string;
  publishedAt: string;
  mandatory?: boolean;
}

function updateManifestConfiguration(env: Env): UpdateManifestConfiguration {
  let parsed: Partial<UpdateManifestConfiguration>;
  try { parsed = JSON.parse(env.UPDATE_MANIFEST_JSON) as Partial<UpdateManifestConfiguration>; }
  catch { throw new Error("UPDATE_MANIFEST_JSON is not valid JSON"); }
  const sha256Hex = String(parsed.sha256 ?? "").toLowerCase();
  if (
    parsed.platform !== "macos-arm64" || parsed.bundleIdentifier !== "top.aizs.my-bio-tools" ||
    !/^\d+\.\d+\.\d+$/u.test(String(parsed.appVersion ?? "")) ||
    !Number.isSafeInteger(parsed.build) || Number(parsed.build) <= 0 ||
    !Number.isSafeInteger(parsed.size) || Number(parsed.size) <= 0 ||
    !/^[a-f0-9]{64}$/u.test(sha256Hex) || !String(parsed.r2Key ?? "").startsWith("releases/") ||
    !String(parsed.releaseNotes ?? "").trim() || !String(parsed.publishedAt ?? "").trim()
  ) {
    throw new Error("UPDATE_MANIFEST_JSON is incomplete or unsafe");
  }
  return { ...parsed, sha256: sha256Hex } as UpdateManifestConfiguration;
}

async function handleAppUpdate(request: Request, env: Env): Promise<Response> {
  await requireUser(request, env);
  const config = updateManifestConfiguration(env);
  const now = nowSeconds();
  const claims: UpdateManifestClaims = {
    typ: "app-update", iat: now, exp: now + 24 * 60 * 60, schema_version: 1,
    platform: config.platform, bundle_identifier: config.bundleIdentifier,
    app_version: config.appVersion, build: config.build,
    minimum_system_version: config.minimumSystemVersion,
    size: config.size, sha256: config.sha256, r2_key: config.r2Key,
    release_notes: config.releaseNotes, published_at: config.publishedAt,
    mandatory: config.mandatory === true,
  };
  return json({ manifestToken: await signJWT(claims, env.LICENSE_PRIVATE_JWK) });
}

async function handleAppUpdateDownload(request: Request, env: Env): Promise<Response> {
  await requireUser(request, env);
  const config = updateManifestConfiguration(env);
  const object = await env.RELEASES.get(config.r2Key);
  if (!object) return error("UPDATE_NOT_FOUND", "更新安装包尚未发布。", 404);
  if (object.size !== config.size) return error("UPDATE_SIZE_MISMATCH", "更新安装包大小与发布清单不一致。", 503);
  const headers = new Headers({
    "content-type": "application/x-apple-diskimage",
    "content-length": String(object.size),
    "content-disposition": `attachment; filename="My-Bio-Tools-${config.appVersion}-arm64.dmg"`,
    "cache-control": "private, no-store",
    etag: object.httpEtag,
  });
  object.writeHttpMetadata(headers);
  return new Response(object.body, { headers });
}

async function handleLogout(request: Request, env: Env): Promise<Response> {
  const context = await requireUser(request, env);
  await new Repository(env.DB).revokeSession(context.session.id, nowSeconds());
  return json({ message: "已退出登录。" });
}

async function handleForgotPassword(request: Request, env: Env): Promise<Response> {
  const body = await readJson<{ email?: string }>(request);
  const email = normalizeEmail(body.email ?? "");
  await enforceRateLimit(request, env, "forgot-password", email);
  const repository = new Repository(env.DB);
  const user = await repository.getUserByEmail(email);
  if (user && user.status !== "deleted") {
    const now = nowSeconds();
    const token = randomToken();
    await repository.replaceOneTimeToken(user.id, "reset_password", await sha256(token), now, now + PASSWORD_RESET_SECONDS);
    try { await sendResetEmail(env, user, token); }
    catch (emailError) { console.error("reset_email_failed", { userId: user.id, name: (emailError as Error).name }); }
  }
  return json({ message: "如果该邮箱已注册，系统将发送密码重置邮件。" }, 202);
}

function handleResetPasswordGet(url: URL): Response {
  const token = url.searchParams.get("token") ?? "";
  return token ? resetPasswordForm(token) : page("重置失败", "<h1>链接无效</h1>", 400);
}

async function handleResetPasswordPost(request: Request, env: Env): Promise<Response> {
  const contentType = request.headers.get("content-type") ?? "";
  let tokenValue = ""; let password = ""; let confirm = "";
  if (contentType.includes("application/json")) {
    const body = await readJson<{ token?: string; password?: string; confirm?: string }>(request);
    tokenValue = body.token ?? ""; password = body.password ?? ""; confirm = body.confirm ?? password;
  } else {
    const form = await request.formData();
    tokenValue = String(form.get("token") ?? ""); password = String(form.get("password") ?? "");
    confirm = String(form.get("confirm") ?? "");
  }
  const passwordError = validatePassword(password);
  if (passwordError || password !== confirm) return resetPasswordForm(tokenValue, `<p class="error">${passwordError ?? "两次密码不一致。"}</p>`);
  const repository = new Repository(env.DB);
  const now = nowSeconds();
  const token = await repository.getValidToken(await sha256(tokenValue), "reset_password", now);
  if (!token) return page("重置失败", "<h1>链接已失效</h1><p>请重新申请密码重置。</p>", 400);
  const salt = randomToken(16);
  if (!(await repository.resetPassword(token, await hashPassword(password, salt, env.PASSWORD_PEPPER), salt, now))) {
    return page("重置失败", "<h1>无法重置密码</h1>", 400);
  }
  await writeAudit(repository, request, env, {
    actorType: "user", actorId: token.user_id, action: "reset_password", targetType: "user", targetId: token.user_id,
  });
  return page("密码已重置", "<h1>密码已更新</h1><p>所有已登录设备已退出，请返回 APP 重新登录。</p>");
}

async function handleAdmin(request: Request, env: Env, url: URL): Promise<Response> {
  const adminEmail = await requireAdmin(request, env);
  const repository = new Repository(env.DB);
  if ((url.pathname === "/admin" || url.pathname === "/admin/") && request.method === "GET") {
    const status = url.searchParams.get("status") ?? "pending";
    const allowed = new Set(["all", "unverified", "pending", "active", "rejected", "suspended"]);
    const selectedStatus = allowed.has(status) ? status : "pending";
    const query = (url.searchParams.get("q") ?? "").trim().slice(0, 100);
    const users = await repository.listUsers(selectedStatus, query);
    const devicesByUser: Record<string, Awaited<ReturnType<Repository["listDevices"]>>> = {};
    const expandedUserId = url.searchParams.get("devices") ?? "";
    if (users.some((user) => user.id === expandedUserId)) devicesByUser[expandedUserId] = await repository.listDevices(expandedUserId);
    return adminDashboard(users, await repository.userStatusCounts(), selectedStatus, query, devicesByUser, url.searchParams.get("message") ?? "");
  }
  if (url.pathname === "/admin/action" && request.method === "POST") {
    const form = await request.formData();
    const action = String(form.get("action") ?? "");
    const userId = String(form.get("userId") ?? "");
    const headers = new Headers(request.headers);
    headers.set("content-type", "application/json");
    let target = ""; let method = "POST"; let body: Record<string, string> | undefined; let message = "操作已完成。";
    if (action === "set_status") {
      target = `/api/v1/admin/members/${encodeURIComponent(userId)}/status`; method = "PATCH";
      body = { status: String(form.get("status") ?? ""), reason: String(form.get("reason") ?? "") };
      message = "账号状态已更新。";
    } else if (action === "force_logout") {
      target = `/api/v1/admin/members/${encodeURIComponent(userId)}/force-logout`; message = "已强制退出该账号的全部设备。";
    } else if (action === "send_password_reset") {
      target = `/api/v1/admin/members/${encodeURIComponent(userId)}/send-password-reset`; message = "密码重置邮件已发送。";
    } else if (action === "delete_user") {
      target = `/api/v1/admin/members/${encodeURIComponent(userId)}`; method = "DELETE";
      body = { email: String(form.get("email") ?? ""), confirmation: String(form.get("confirmation") ?? "") };
      message = "账号已永久删除并匿名化。";
    } else if (action === "revoke_device") {
      target = `/api/v1/admin/members/${encodeURIComponent(userId)}/devices/${encodeURIComponent(String(form.get("deviceId") ?? ""))}`; method = "DELETE";
      message = "设备已解绑。";
    } else {
      return error("INVALID_ADMIN_ACTION", "管理操作无效。", 400);
    }
    const proxyUrl = new URL(target, url);
    const proxyRequest = new Request(proxyUrl, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    const response = await handleAdmin(proxyRequest, env, proxyUrl);
    if (!response.ok) return response;
    return new Response(null, { status: 303, headers: { location: `/admin/?message=${encodeURIComponent(message)}` } });
  }
  if (
    (url.pathname === "/api/v1/admin/users" || url.pathname === "/api/v1/admin/members") &&
    request.method === "GET"
  ) {
    const status = url.searchParams.get("status") ?? "pending";
    const allowed = new Set(["all", "unverified", "pending", "active", "rejected", "suspended"]);
    if (!allowed.has(status)) return error("INVALID_STATUS", "账号状态无效。", 400);
    const users = await repository.listUsers(status, (url.searchParams.get("q") ?? "").trim().slice(0, 100));
    return json({ users: users.map(publicUser), counts: await repository.userStatusCounts() });
  }
  if (url.pathname === "/api/v1/admin/audit-logs" && request.method === "GET") return json({ logs: await repository.listAuditLogs() });
  const statusMatch = url.pathname.match(/^\/api\/v1\/admin\/(?:users|members)\/([^/]+)\/status$/u);
  if (statusMatch && request.method === "PATCH") {
    const body = await readJson<{ status?: string; reason?: string }>(request);
    if (body.status !== "active" && body.status !== "rejected" && body.status !== "suspended") return error("INVALID_STATUS", "只能批准、拒绝或停用账号。", 400);
    const reason = (body.reason ?? "").trim().slice(0, 500);
    const previousUser = await repository.getUserById(statusMatch[1]);
    if (!(await repository.setUserStatus(statusMatch[1], body.status, reason, adminEmail, nowSeconds()))) return error("USER_NOT_FOUND", "账号不存在或尚未验证邮箱。", 404);
    const user = await repository.getUserById(statusMatch[1]);
    await writeAudit(repository, request, env, {
      actorType: "admin", actorId: adminEmail, action: `set_status_${body.status}`,
      targetType: "user", targetId: statusMatch[1], metadata: { reason },
    });
    if (user) try { await sendReviewNotice(env, user, body.status, reason, previousUser?.status); }
    catch (emailError) { console.error("review_notice_failed", { userId: user.id, name: (emailError as Error).name }); }
    return json({ user: user ? publicUser(user) : null });
  }
  const forceLogoutMatch = url.pathname.match(/^\/api\/v1\/admin\/(?:users|members)\/([^/]+)\/force-logout$/u);
  if (forceLogoutMatch && request.method === "POST") {
    await repository.revokeAllSessions(forceLogoutMatch[1], nowSeconds());
    await writeAudit(repository, request, env, {
      actorType: "admin", actorId: adminEmail, action: "force_logout", targetType: "user", targetId: forceLogoutMatch[1],
    });
    return json({ message: "已撤销该账号的所有会话。" });
  }
  const resetMatch = url.pathname.match(/^\/api\/v1\/admin\/(?:users|members)\/([^/]+)\/send-password-reset$/u);
  if (resetMatch && request.method === "POST") {
    const user = await repository.getUserById(resetMatch[1]);
    if (!user || user.status === "deleted") return error("USER_NOT_FOUND", "账号不存在。", 404);
    const now = nowSeconds();
    const token = randomToken();
    await repository.replaceOneTimeToken(user.id, "reset_password", await sha256(token), now, now + PASSWORD_RESET_SECONDS);
    await sendResetEmail(env, user, token);
    await writeAudit(repository, request, env, {
      actorType: "admin", actorId: adminEmail, action: "send_password_reset",
      targetType: "user", targetId: user.id,
    });
    return json({ message: "密码重置邮件已发送。" });
  }
  const deleteMatch = url.pathname.match(/^\/api\/v1\/admin\/(?:users|members)\/([^/]+)$/u);
  if (deleteMatch && request.method === "DELETE") {
    const body = await readJson<{ email?: string; confirmation?: string }>(request);
    const user = await repository.getUserById(deleteMatch[1]);
    if (!user || user.status === "deleted") return error("USER_NOT_FOUND", "账号不存在。", 404);
    if (normalizeEmail(body.email ?? "") !== user.email || body.confirmation !== "DELETE") {
      return error("DELETE_CONFIRMATION_REQUIRED", "必须同时确认账号邮箱和 DELETE 口令。", 400);
    }
    const auditTarget = user.id;
    if (!(await repository.permanentlyDeleteUser(user.id, nowSeconds()))) return error("USER_NOT_FOUND", "账号不存在。", 404);
    await writeAudit(repository, request, env, {
      actorType: "admin", actorId: adminEmail, action: "permanently_delete_user",
      targetType: "deleted_user", targetId: auditTarget,
    });
    return json({ message: "账号个人信息已匿名化，设备、会话和临时令牌已清除。" });
  }
  const devicesMatch = url.pathname.match(/^\/api\/v1\/admin\/(?:users|members)\/([^/]+)\/devices$/u);
  if (devicesMatch && request.method === "GET") return json({ devices: await repository.listDevices(devicesMatch[1]) });
  const revokeDeviceMatch = url.pathname.match(/^\/api\/v1\/admin\/(?:users|members)\/([^/]+)\/devices\/([^/]+)$/u);
  if (revokeDeviceMatch && request.method === "DELETE") {
    if (!(await repository.revokeDevice(revokeDeviceMatch[1], revokeDeviceMatch[2], nowSeconds()))) return error("DEVICE_NOT_FOUND", "设备不存在或已解绑。", 404);
    await writeAudit(repository, request, env, {
      actorType: "admin", actorId: adminEmail, action: "revoke_device",
      targetType: "device", targetId: revokeDeviceMatch[2], metadata: { userId: revokeDeviceMatch[1] },
    });
    return json({ message: "设备已解绑。" });
  }
  return error("NOT_FOUND", "未找到管理接口。", 404);
}

async function route(request: Request, env: Env): Promise<Response> {
  const url = new URL(request.url);
  if (url.pathname.startsWith("/admin") || url.pathname.startsWith("/api/v1/admin/")) return handleAdmin(request, env, url);
  if (url.pathname === "/health" && request.method === "GET") {
    await validateSigningConfiguration(env.LICENSE_PRIVATE_JWK, env.LICENSE_PUBLIC_JWK);
    validatedOmicsDatabaseKey(env.OMICS_DATABASE_KEY_B64);
    updateManifestConfiguration(env);
    return json({ status: "ok", version: "1.9.0", licenseSigning: "ok", omicsKeyDelivery: "ok", appUpdate: "ok" });
  }
  if (url.pathname === "/verify-email" && request.method === "GET") return handleVerifyEmail(url, request, env);
  if (url.pathname === "/reset-password" && request.method === "GET") return handleResetPasswordGet(url);
  if (url.pathname === "/reset-password" && request.method === "POST") return handleResetPasswordPost(request, env);
  if (url.pathname === "/api/v1/register" && request.method === "POST") return handleRegister(request, env);
  if (url.pathname === "/api/v1/email/resend" && request.method === "POST") return handleResendVerification(request, env);
  if (url.pathname === "/api/v1/login" && request.method === "POST") return handleLogin(request, env);
  if (url.pathname === "/api/v1/token/refresh" && request.method === "POST") return handleRefresh(request, env);
  if (url.pathname === "/api/v1/license/renew" && request.method === "POST") return handleRenewLicense(request, env);
  if (url.pathname === "/api/v1/logout" && request.method === "POST") return handleLogout(request, env);
  if (url.pathname === "/api/v1/password/forgot" && request.method === "POST") return handleForgotPassword(request, env);
  if (url.pathname === "/api/v1/password/reset" && request.method === "POST") return handleResetPasswordPost(request, env);
  if (url.pathname === "/api/v1/me" && request.method === "GET") return handleMe(request, env);
  if (url.pathname === "/api/v1/me/devices" && request.method === "GET") return handleDevices(request, env);
  if (url.pathname === "/api/v1/app-update" && request.method === "GET") return handleAppUpdate(request, env);
  if (url.pathname === "/api/v1/app-update/download" && request.method === "GET") return handleAppUpdateDownload(request, env);
  const deviceMatch = url.pathname.match(/^\/api\/v1\/me\/devices\/([^/]+)$/u);
  if (deviceMatch && request.method === "DELETE") return handleDeleteDevice(request, env, deviceMatch[1]);
  return error("NOT_FOUND", "未找到请求的资源。", 404);
}

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const requestId = crypto.randomUUID();
    try { return await route(request, env); }
    catch (caught) {
      if (caught instanceof Response) return caught;
      console.error("request_failed", { requestId, path: new URL(request.url).pathname, name: (caught as Error).name });
      return error("INTERNAL_ERROR", "服务暂时不可用，请稍后重试。", 500, requestId);
    }
  },
};
