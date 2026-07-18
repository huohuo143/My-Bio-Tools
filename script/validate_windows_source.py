#!/usr/bin/env python3
"""Statically validate the Windows port and an optional staged distribution."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import struct
import sys
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parent.parent
APP_SOURCE = ROOT / "app_source"
WINDOWS_PROJECT = ROOT / "windows" / "MyBioTools.Windows"

EXPECTED_WINDOWS_FILES = [
    WINDOWS_PROJECT / "MyBioTools.Windows.csproj",
    WINDOWS_PROJECT / "App.xaml",
    WINDOWS_PROJECT / "MainWindow.xaml",
    WINDOWS_PROJECT / "MainWindow.xaml.cs",
    WINDOWS_PROJECT / "AccountWindow.xaml",
    WINDOWS_PROJECT / "AccountWindow.xaml.cs",
    WINDOWS_PROJECT / "GlobalUsings.cs",
    WINDOWS_PROJECT / "BackendController.cs",
    WINDOWS_PROJECT / "AuthModels.cs",
    WINDOWS_PROJECT / "AuthService.cs",
    WINDOWS_PROJECT / "LicenseVerifier.cs",
    WINDOWS_PROJECT / "SecureCredentialStore.cs",
    WINDOWS_PROJECT / "JobObject.cs",
    WINDOWS_PROJECT / "RollingLogWriter.cs",
    WINDOWS_PROJECT / "KnownFolders.cs",
    WINDOWS_PROJECT / "app.manifest",
    WINDOWS_PROJECT / "Assets" / "AppIcon.ico",
    ROOT / "packaging" / "BioToolsBackend.windows.spec",
    ROOT / "packaging" / "windows-version-info.txt",
    ROOT / "windows" / "installer" / "MyBioTools.iss",
    ROOT / "script" / "build_windows.ps1",
    ROOT / "script" / "test_windows_runtime.ps1",
    ROOT / "packaging" / "THIRD_PARTY_NOTICES.txt",
]

DATA_FILES = {
    "IRGSP-1.0_transcript_2025-03-19.fasta.gz": 20_000_000,
    "IRGSP-1.0_gene_2025-03-19.fasta.gz": 39_000_000,
    "IRGSP-1.0_cds_2025-03-19.fasta.gz": 12_000_000,
    "RAP-MSU_2025-03-19.txt.gz": 300_000,
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--staged-app", type=Path)
    parser.add_argument("--report", type=Path)
    return parser.parse_args()


def validate_icon(path: Path, failures: list[str], checks: list[str]) -> None:
    try:
        data = path.read_bytes()
        reserved, image_type, image_count = struct.unpack_from("<HHH", data, 0)
    except Exception as exc:
        failures.append(f"cannot read ICO: {exc}")
        return

    if reserved != 0 or image_type != 1 or image_count < 7:
        failures.append(
            f"ICO is not a multi-resolution Windows icon: type={image_type}, count={image_count}"
        )
    else:
        checks.append(f"multi-resolution ICO: {image_count} images")


def validate_project(failures: list[str], checks: list[str]) -> tuple[int, int]:
    for path in EXPECTED_WINDOWS_FILES:
        if not path.is_file():
            failures.append(f"missing Windows source file: {path.relative_to(ROOT)}")
    if failures:
        return 0, 0

    project = ET.parse(WINDOWS_PROJECT / "MyBioTools.Windows.csproj").getroot()
    for xaml_name in ("App.xaml", "MainWindow.xaml", "AccountWindow.xaml"):
        try:
            ET.parse(WINDOWS_PROJECT / xaml_name)
        except ET.ParseError as exc:
            failures.append(f"invalid {xaml_name}: {exc}")
    values = {
        element.tag.rsplit("}", 1)[-1]: (element.text or "").strip()
        for element in project.iter()
    }
    expected_values = {
        "TargetFramework": "net10.0-windows10.0.17763.0",
        "UseWPF": "true",
        "RuntimeIdentifier": "win-x64",
        "SelfContained": "true",
        "Version": "1.7.2",
    }
    for key, expected in expected_values.items():
        if values.get(key) != expected:
            failures.append(f"csproj {key} should be {expected!r}, found {values.get(key)!r}")

    package_references = [
        element
        for element in project.iter()
        if element.tag.rsplit("}", 1)[-1] == "PackageReference"
    ]
    webview = next(
        (
            element
            for element in package_references
            if element.attrib.get("Include") == "Microsoft.Web.WebView2"
        ),
        None,
    )
    if webview is None or webview.attrib.get("Version") != "1.0.4078.44":
        failures.append("WebView2 SDK must be pinned to 1.0.4078.44")
    else:
        checks.append("WPF target and WebView2 SDK pin")

    main_window = (WINDOWS_PROJECT / "MainWindow.xaml.cs").read_text(encoding="utf-8")
    backend = (WINDOWS_PROJECT / "BackendController.cs").read_text(encoding="utf-8")
    job_object = (WINDOWS_PROJECT / "JobObject.cs").read_text(encoding="utf-8")
    auth_service = (WINDOWS_PROJECT / "AuthService.cs").read_text(encoding="utf-8")
    license_verifier = (WINDOWS_PROJECT / "LicenseVerifier.cs").read_text(encoding="utf-8")
    credential_store = (WINDOWS_PROJECT / "SecureCredentialStore.cs").read_text(encoding="utf-8")
    account_window = (WINDOWS_PROJECT / "AccountWindow.xaml.cs").read_text(encoding="utf-8")
    required_tokens = {
        "external navigation isolation": "IsLocalAppUri",
        "download interception": "Core_DownloadStarting",
        "WebView2 runtime fallback": "InstallRuntimeButton_Click",
        "60-second health timeout": "TimeSpan.FromSeconds(60)",
        "loopback-only backend URL": "http://127.0.0.1",
        "process-tree cleanup": "KillOnJobClose",
        "authorization gate": "ConfigureAuthorization",
        "DPAPI credential storage": "CryptProtectData",
        "offline license verification": "VerifyEd25519",
        "six-hour authorization refresh": "TimeSpan.FromHours(6)",
        "resume authorization refresh": "WmPowerBroadcast",
        "member device unbinding": "DeviceToRevoke",
        "member password reset": "PasswordResetRequested",
    }
    combined = main_window + backend + job_object + auth_service + license_verifier + credential_store + account_window
    for description, token in required_tokens.items():
        if token not in combined:
            failures.append(f"missing Windows behavior: {description}")
        else:
            checks.append(description)

    launcher = (ROOT / "backend" / "launcher.py").read_text(encoding="utf-8")
    if 'args.parent_pid and os.name == "posix"' not in launcher:
        failures.append("backend parent watcher is not guarded for Windows")
    else:
        checks.append("cross-platform backend launcher")

    windows_spec = (ROOT / "packaging" / "BioToolsBackend.windows.spec").read_text(
        encoding="utf-8"
    )
    if "console=True" not in windows_spec:
        failures.append("Windows backend must retain redirectable stdout/stderr")
    else:
        checks.append("redirectable hidden Windows backend console")
    if "TOOLS_BY_MODULE" not in windows_spec:
        failures.append("Windows PyInstaller spec does not discover modules from tool_catalog")
    else:
        checks.append("catalog-driven PyInstaller hidden imports")
    if (
        "module_collection_mode" not in windows_spec
        or '"docx": "pyz+py"' not in windows_spec
    ):
        failures.append("Windows PyInstaller spec does not preserve python-docx package directories")
    else:
        checks.append("python-docx frozen package layout")

    build_script = (ROOT / "script" / "build_windows.ps1").read_text(encoding="utf-8")
    installer_script = (ROOT / "windows" / "installer" / "MyBioTools.iss").read_text(
        encoding="utf-8"
    )
    distribution_tokens = [
        "pyinstaller==6.21.0",
        "dotnet",
        "Compress-Archive",
        "Get-AuthenticodeSignature",
        "SHA256SUMS.txt",
        "validate_ricedata_live.py",
        "validate_efp_live.py",
        "PrivilegesRequired=lowest",
        "ArchitecturesAllowed=x64compatible",
        "MicrosoftEdgeWebView2RuntimeInstallerX64.exe",
        "F3017226-FE2A-4295-8BDF-00C3A9A7E4C5",
    ]
    distribution_source = build_script + installer_script
    missing_distribution_tokens = [
        token for token in distribution_tokens if token not in distribution_source
    ]
    if missing_distribution_tokens:
        failures.append(
            "Windows build/installer contract is incomplete: "
            + ", ".join(missing_distribution_tokens)
        )
    else:
        checks.append("portable ZIP, installer, prerequisite and checksum contract")

    sys.path.insert(0, str(APP_SOURCE))
    from tool_catalog import functional_tools  # noqa: PLC0415

    tools = functional_tools()
    tool_count = len(tools)
    online_count = sum(tool.requires_internet for tool in tools)
    if tool_count != 7:
        failures.append(f"expected 7 functional tools, found {tool_count}")
    if online_count != 2:
        failures.append(f"expected 2 online tools, found {online_count}")
    incomplete_explanations = [
        tool.name
        for tool in tools
        if not all((tool.inputs, tool.method, tool.outputs, tool.cautions))
    ]
    if incomplete_explanations:
        failures.append(
            "tool explanation fields are incomplete: " + ", ".join(incomplete_explanations)
        )
    else:
        checks.append("all tool input, method, output and caution explanations")
    checks.append(f"tool catalog: {tool_count} tools, {online_count} online")

    removed_pages = ["tool_b.py", "tool_c.py", "extract_rows_csv.py"]
    unexpected = [name for name in removed_pages if (APP_SOURCE / name).exists()]
    if unexpected:
        failures.append("removed file/table pages are still present: " + ", ".join(unexpected))
    else:
        checks.append("removed file/table pages are absent")

    nl_source = APP_SOURCE / "vendor" / "nlstradamus" / "NLStradamus.cpp"
    build_script_text = (ROOT / "script" / "build_windows.ps1").read_text(encoding="utf-8")
    if not nl_source.is_file() or "g++.exe" not in build_script_text or "NLStradamus.exe" not in build_script_text:
        failures.append("Windows NLStradamus source-build contract is incomplete")
    else:
        checks.append("Windows NLStradamus 1.8 source-build contract")

    validate_icon(WINDOWS_PROJECT / "Assets" / "AppIcon.ico", failures, checks)
    return tool_count, online_count


def validate_staged_app(
    staged_app: Path,
    failures: list[str],
    checks: list[str],
) -> None:
    required = [
        staged_app / "My Bio Tools.exe",
        staged_app / "backend" / "BioToolsBackend.exe",
        staged_app / "app_source" / "main.py",
        staged_app / "prerequisites" / "MicrosoftEdgeWebView2RuntimeInstallerX64.exe",
        staged_app / "THIRD_PARTY_NOTICES.txt",
        staged_app / "version-manifest.json",
        staged_app / "auth-config.json",
    ]
    for path in required:
        if not path.is_file():
            failures.append(f"missing staged distribution file: {path}")

    docx_runtime = staged_app / "backend" / "_internal" / "docx"
    if not (docx_runtime / "parts").is_dir():
        failures.append(f"missing staged python-docx directory: {docx_runtime / 'parts'}")
    for template in ("default-header.xml", "default-footer.xml"):
        raw_template = docx_runtime / "parts" / ".." / "templates" / template
        if not raw_template.is_file():
            failures.append(
                "staged python-docx template is unreadable through its runtime path: "
                f"{raw_template}"
            )
    if not (docx_runtime / "templates" / "default.docx").is_file():
        failures.append("missing staged python-docx default.docx template")

    data_dir = staged_app / "app_source" / "data" / "Rice_Genome_Annotation_Project"
    for filename, minimum_size in DATA_FILES.items():
        path = data_dir / filename
        if not path.is_file():
            failures.append(f"missing staged data file: {path}")
        elif path.stat().st_size < minimum_size:
            failures.append(f"staged data file is unexpectedly small: {path}")

    installer = required[3]
    if installer.is_file() and installer.stat().st_size < 1_000_000:
        failures.append("staged WebView2 standalone installer is unexpectedly small")

    manifest_path = staged_app / "version-manifest.json"
    if manifest_path.is_file():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
            if manifest.get("version") != "1.7.2" or manifest.get("platform") != "win-x64":
                failures.append("staged version manifest has unexpected version or platform")
        except Exception as exc:
            failures.append(f"cannot parse staged version manifest: {exc}")

    auth_config_path = staged_app / "auth-config.json"
    if auth_config_path.is_file():
        try:
            auth_config = json.loads(auth_config_path.read_text(encoding="utf-8-sig"))
            if auth_config.get("baseUrl") != "https://mybiotools.aizs.top":
                failures.append("staged auth config has unexpected service URL")
            public_jwk = json.loads(auth_config.get("publicJwk", ""))
            if public_jwk.get("kty") != "OKP" or public_jwk.get("crv") != "Ed25519" or not public_jwk.get("x"):
                failures.append("staged auth config does not contain an Ed25519 public JWK")
        except Exception as exc:
            failures.append(f"cannot parse staged auth config: {exc}")

    if not failures:
        checks.append("staged WPF, backend, prerequisite, notices and manifest")
        checks.append("four staged rice annotation data files")


def write_report(
    path: Path,
    checks: list[str],
    failures: list[str],
    staged_app: Path | None,
) -> None:
    lines = [
        "# My Bio Tools Windows 构建验证报告",
        "",
        f"- 源码目录：`{ROOT}`",
        f"- 分发目录：`{staged_app}`" if staged_app else "- 分发目录：未提供，仅验证源码",
        f"- 通过检查：{len(checks)}",
        f"- 失败检查：{len(failures)}",
        "",
        "## 已通过",
        "",
        *[f"- {item}" for item in checks],
        "",
        "## 未通过",
        "",
        *([f"- {item}" for item in failures] if failures else ["- 无"]),
        "",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    args = parse_args()
    failures: list[str] = []
    checks: list[str] = []

    validate_project(failures, checks)
    staged_app = args.staged_app.expanduser().resolve() if args.staged_app else None
    if staged_app:
        validate_staged_app(staged_app, failures, checks)

    for item in checks:
        print(f"PASS {item}")
    for item in failures:
        print(f"FAIL {item}", file=sys.stderr)

    if args.report:
        write_report(args.report, checks, failures, staged_app)
        print(f"report: {args.report}")

    if failures:
        print(f"\nWindows validation failed: {len(failures)} issue(s)", file=sys.stderr)
        return 1

    print(f"\nWindows validation passed: {len(checks)} checks")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
