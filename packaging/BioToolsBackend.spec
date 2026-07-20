from pathlib import Path

from PyInstaller.utils.hooks import collect_all, copy_metadata


ROOT = Path(SPECPATH).parent
APP_SOURCE = ROOT / "app_source"
LAUNCHER = ROOT / "backend" / "launcher.py"

datas = []
binaries = []
hiddenimports = [
    "license_gate",
    "omics_unlock",
    "app_ui",
    "appearance_preferences",
    "analysis_explanations",
    "welcome",
    "methods_guide",
    "tool_a",
    "primer_design",
    "extract_fasta",
    "fasta_rename",
    "RiceData_crawler",
    "RAP_MSU_convert",
    "rice_seq_extractor",
    "RGAP_sequence_downloader",
    "rice_utr_promoter_downloader",
    "rice_gene_core",
    "prediction_services",
    "prediction_visualization",
    "codex_chatgpt",
    "llm_providers",
    "model_preferences",
    "report_interpretation",
    "mechanism_evidence",
    "report_builder",
    "rice_gene_analysis",
    "rice_efp",
    "lab_omics",
    "analysis_jobs",
    "job_ui",
    "unittest",
    "pyparsing.testing",
    "matplotlib.backends.backend_agg",
    "matplotlib.backends.backend_pdf",
    "matplotlib.backends.backend_svg",
]

streamlit_datas, streamlit_binaries, streamlit_hiddenimports = collect_all(
    "streamlit"
)
streamlit_hiddenimports = [
    module
    for module in streamlit_hiddenimports
    if not module.startswith(
        (
            "streamlit.hello",
            "streamlit.testing",
            "streamlit.external.langchain",
        )
    )
]
datas += streamlit_datas
binaries += streamlit_binaries
hiddenimports += streamlit_hiddenimports

# primer3-py imports ``primer3.bindings`` from a guarded try/except block.
# PyInstaller cannot discover that import reliably, so collect the complete
# package (including its compiled Cython extensions and thermodynamic data).
primer3_datas, primer3_binaries, primer3_hiddenimports = collect_all("primer3")
datas += primer3_datas
binaries += primer3_binaries
hiddenimports += primer3_hiddenimports

biolib_datas, biolib_binaries, biolib_hiddenimports = collect_all("biolib")
datas += biolib_datas
binaries += biolib_binaries
hiddenimports += biolib_hiddenimports

for distribution in [
    "streamlit",
    "altair",
    "pyarrow",
    "pydeck",
    "pandas",
    "numpy",
    "biopython",
    "primer3-py",
    "beautifulsoup4",
    "requests",
    "python-docx",
    "openpyxl",
    "matplotlib",
    "pybiolib",
]:
    try:
        datas += copy_metadata(distribution, recursive=True)
    except Exception:
        pass

analysis = Analysis(
    [str(LAUNCHER)],
    pathex=[str(APP_SOURCE), str(ROOT / "backend")],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    # python-docx 1.2 resolves header/footer templates through
    # ``docx/parts/../templates``. Keep the package sources beside the
    # collected templates so that the intermediate ``parts`` directory exists
    # in the frozen runtime.
    module_collection_mode={"docx": "pyz+py", "unittest": "py"},
    runtime_hooks=[],
    excludes=[
        "IPython",
        "jupyter",
        "notebook",
        "pytest",
        "streamlit.hello",
        "streamlit.testing",
        "streamlit.external.langchain",
        "tkinter",
    ],
    noarchive=False,
    optimize=1,
)

pyz = PYZ(analysis.pure)

exe = EXE(
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="BioToolsBackend",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

bundle = COLLECT(
    exe,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="BioToolsBackend",
)
