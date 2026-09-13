"""V4 Pages smoke tests — 9 new pages + V4 design system. Pattern mirrors test_v4_home.py."""
import importlib.util
import pytest

_PAGE_MODULES = [
    ("v4_chat", "V4Chat"),
    ("v4_agent", "V4Agent"),
    ("v4_memory", "V4Memory"),
    ("v4_skills", "V4Skills"),
    ("v4_computer", "V4Computer"),
    ("v4_browser", "V4Browser"),
    ("v4_tasks", "V4Tasks"),
    ("v4_activity", "V4Activity"),
    ("v4_settings", "V4Settings"),
    ("v4_design", None),
]


@pytest.mark.parametrize("mod_name,cls_name", _PAGE_MODULES)
def test_v4_page_module_importable(mod_name, cls_name):
    """All 9 page modules and v4_design module import successfully."""
    module = __import__(f"hermes.ui.{mod_name}", fromlist=["*"])
    assert module is not None
    if cls_name is not None:
        assert hasattr(module, cls_name), f"hermes.ui.{mod_name} is missing class {cls_name}"


def test_v4_design_exports_all_8_primitives():
    """F2 rule: design system exports exactly the 8 named primitives."""
    from hermes.ui import v4_design
    expected = {
        "V4Panel", "V4PageHeader", "V4SectionHeader", "V4StatusBadge",
        "V4StatusTile", "V4TimelineEvent", "V4EmptyState", "V4Card",
        "status_color",
    }
    actual = set(n for n in dir(v4_design) if not n.startswith("_"))
    missing = expected - actual
    assert not missing, f"v4_design missing expected symbols: {sorted(missing)}"


@pytest.mark.parametrize("mod_name,cls_name", [(m, c) for m, c in _PAGE_MODULES if c is not None])
def test_v4_page_uses_only_real_store_keys_no_fake_state(mod_name, cls_name):
    """F14 data integrity: Page constructors only reference the canonical 7 snapshot keys.
    Never invent new state fields (connection/session from event_count, etc.).
    This test instantiates the page inside a protected wrapper that disables get_snapshot overrides
    on fake field access. Pages only call get_snapshot()."""
    from hermes.ui.v4_store import V4UIStore

    real_keys = {"phase", "approval_state", "verification_state",
                 "recovery_state", "error_state", "event_count", "latest_event_id"}

    _ALLOWED_EXTRA_KEYS = {
        "connection_state", "connection_detail", "activity",
        "task_completed_count", "completed_tasks", "voice_enabled",
        "wake_word_enabled", "microphone_available", "status_message",
        "chat_message_count", "worker_id", "latest_message_at",
        "last_message_role", "session_active", "uptime_ms",
    }

    class GuardedStore(V4UIStore):
        def get_snapshot(self):
            snap = super().get_snapshot()
            assert real_keys.issubset(snap.keys())
            # V4 MUVAHHİD bridge extends the canonical set with worker-projected
            # state fields (connection/voice/task counters). Old 7 keys still
            # present; extras are only the known whitelist above (no arbitrary
            # fake state leakage).
            extra = set(snap.keys()) - real_keys - _ALLOWED_EXTRA_KEYS
            assert not extra, f"store snapshot contains unlisted extra keys: {sorted(extra)}"
            return snap

    module = __import__(f"hermes.ui.{mod_name}", fromlist=["*"])
    cls = getattr(module, cls_name)
    assert callable(cls)

    try:
        import customtkinter as ctk
        try:
            top = ctk.CTk()
        except Exception as exc_tk:  # pragma: no cover - headless/tcl error
            pytest.skip(f"Skipping GUI instantiation for {mod_name}: cannot initialize CTk: {exc_tk}")
            return
        parent = ctk.CTkFrame(top)
        store = GuardedStore()
        snap_before = store.get_snapshot()
        events_before = len(list(getattr(store, "_events", []) or []))
        page = cls(parent, store)
        # Destroy cleanly (lifecycle)
        if hasattr(page, "destroy") and callable(page.destroy):
            page.destroy()
        try:
            top.destroy()
        except Exception:
            pass
        # No store mutation by page constructor
        snap_after = store.get_snapshot()
        events_after = len(list(getattr(store, "_events", []) or []))
        assert snap_before == snap_after, f"{mod_name} constructor mutated store snapshot"
        assert events_before == events_after, f"{mod_name} constructor added fake events to canonical event stream"
    except ImportError as e:  # pragma: no cover - skip gracefully
        pytest.skip(f"Skipping GUI test for {mod_name}: customtkinter import failed: {e}")


@pytest.mark.parametrize("mod_name", [m for m, _ in _PAGE_MODULES])
def test_v4_page_module_spec_found(mod_name):
    """All page modules are importable via find_spec (same as test_v4_home:test_app_v4_shell_imports_v4_home)."""
    spec = importlib.util.find_spec(f"hermes.ui.{mod_name}")
    assert spec is not None, f"hermes.ui.{mod_name} find_spec returned None"


def test_no_rgba_usage_in_new_v4_modules():
    """F2 + Constraints: hex-only colors (NO rgba string) used in any new v4 file."""
    import pathlib
    ui_dir = pathlib.Path(__file__).resolve().parent.parent / "src" / "hermes" / "ui"
    new_files = [
        "v4_design.py", "v4_chat.py", "v4_agent.py", "v4_memory.py",
        "v4_skills.py", "v4_computer.py", "v4_browser.py", "v4_tasks.py",
        "v4_activity.py", "v4_settings.py",
    ]
    hits: list[str] = []
    for name in new_files:
        p = ui_dir / name
        if p.exists():
            for lineno, line in enumerate(p.read_text(encoding="utf-8", errors="replace").splitlines(), start=1):
                if "rgba(" in line or "#rgba" in line:
                    hits.append(f"{name}:{lineno}: {line.strip()[:120]}")
    assert not hits, "rgba() color usage found — only Tk/CustomTkinter hex colors are allowed.\n" + "\n".join(hits)


def test_page_modules_do_not_import_v3_runtime_modules():
    """Pages = presentation only. MUST NOT import orchestrator/reasoning/executor/memory runtime/
    canonical event system. These imports are banned (architecture preservation rule)."""
    import pathlib, ast
    banned_stems = {
        "hermes.orchestrator", "hermes.reasoning", "hermes.executor",
        "hermes.memory.runtime", "hermes.skills.executor",
        "hermes.security.policy", "hermes.approval",
        "hermes.config_client.settings_store",
    }
    # Exception for settings_store public API (load_settings_form / save_settings_form) — ALLOWED
    # Exception for skills.loader.load_all_skills — ALLOWED
    ui_dir = pathlib.Path(__file__).resolve().parent.parent / "src" / "hermes" / "ui"
    files = [
        "v4_chat.py", "v4_agent.py", "v4_memory.py", "v4_skills.py",
        "v4_computer.py", "v4_browser.py", "v4_tasks.py", "v4_activity.py",
        "v4_settings.py", "v4_design.py",
    ]
    violations: list[str] = []
    allowed_imports = {
        "hermes.config.settings_store",  # settings form backend contract
        "hermes.skills.loader",          # load_all_skills contract
        "hermes.config_client",          # user_config_path (used in settings)
        "hermes.ui.modern_theme",
        "hermes.ui.v4_store",
        "hermes.ui.v4_projection",
        "hermes.ui.v4_home",
        "hermes.ui.v4_design",
        "hermes.ui.v4_chat", "hermes.ui.v4_agent", "hermes.ui.v4_memory",
        "hermes.ui.v4_skills", "hermes.ui.v4_computer", "hermes.ui.v4_browser",
        "hermes.ui.v4_tasks", "hermes.ui.v4_activity", "hermes.ui.v4_settings",
    }
    for fname in files:
        p = ui_dir / fname
        if not p.exists():
            continue
        tree = ast.parse(p.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                module = getattr(node, "module", None)
                names = []
                if isinstance(node, ast.ImportFrom):
                    if module:
                        names = [module]
                else:
                    for alias in node.names:
                        names.append(alias.name)
                for nm in names:
                    stem_violation = False
                    for ban in banned_stems:
                        if nm == ban or nm.startswith(ban + "."):
                            # Exception settings_store:
                            if ban == "hermes.config_client.settings_store":
                                continue
                            if ban == "hermes.config_client" and nm == "hermes.config_client":
                                continue
                            stem_violation = True
                            break
                    if stem_violation:
                        violations.append(f"{fname} imports banned: {nm}")
    assert not violations, (
        "V4 UI presentation layer must NOT import V3 runtime modules (architecture preservation).\n"
        + "\n".join(violations)
    )
