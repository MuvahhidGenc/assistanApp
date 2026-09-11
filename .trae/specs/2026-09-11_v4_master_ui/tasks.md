# Tasks: Hermes V4 — Complete UI/UX Master Implementation
Spec file: `spec.md` (2026-09-11). Every task maps to at least one AC.

---

## Priorities & Execution Order (atomic, dependency-ordered)

---

### Task 1: Reusable V4 Design System Primitives Module
**Status:** pending
**Priority:** high
**Maps ACs:** F2 (rule) — V4Panel, V4PageHeader, V4SectionHeader, V4StatusBadge, V4StatusTile, V4TimelineEvent, V4EmptyState, V4Card.

**Implementation plan:**
1. Create `src/hermes/ui/v4_design.py` (NEW FILE, minimal; NO backend coupling).
2. Use existing `modern_theme.py` constants (CARD, CARD_BORDER, BG, BG_DEEP, NEON_CYAN, NEON_BLUE, HOLO_GREEN, NEON_GOLD, NEON_MAGENTA, TEXT_BRIGHT, TEXT, MUTED, FONT_MONO).
3. NO `rgba()` anywhere; hex only.
4. Class-style primitives (CTkFrame subclasses preferred; expose `widget` attribute or direct subclass).

**Test Requirements (TR):**
- TR (rule): All 8 classes/functions import without CustomTkinter failure (fallback `ctk = None` pattern same as v4_home; v4_home pattern already handles headless import).
- TR (rubric 0-2 pass >=1.5): Primitive colors/fonts consistent (all cards use CARD border CARD_BORDER radius 10-14; badges chip-style Consolas 9 bold uppercase; empty state uses green dot + SYSTEM READY 2-line style)

**Dependencies:** None (Task 1 first).
**Blocked By:** None.

---

### Task 2: Minimal App Shell Modification — Page Registry Mount for 9 Pages
**Status:** pending
**Priority:** high
**Maps ACs:** F1 (rule)

**Implementation plan:**
1. File: `src/hermes/ui/app_v4_shell.py` (existing — MINIMAL EDITS ONLY; no architecture rewrite).
2. Keep `NAV_ITEMS` EXACTLY: `["HOME","CHAT","AGENT","MEMORY","SKILLS","COMPUTER","BROWSER","TASKS","ACTIVITY","SETTINGS"]`.
3. At top: add imports for 9 new page classes (v4_chat through v4_settings; wrapped in try/except to avoid import crash same pattern as ctk = None).
4. Add a page registry constant (dict[str, PageClass]) mapping NAV_ITEMS[1:] → respective PageClass.
5. Replace `_set_nav(item)` branch for non-HOME (L57-59 old fallback `CTkLabel "V4 SHELL • {item}"` → mount registry[item](self.content, self.store) same as HOME pattern.
6. Add destroy() for mounted page before destroying content children (if page has destroy call it). Safe lifecycle.
7. Header status (L30) keep same; may reuse store.snapshot values.

**Test Requirements (TR):**
- TR (rule): AST test from test_app_v4_shell line 5-16 still passes (NAV_ITEMS list unchanged).
- TR (rule): test `_set_nav("CHAT") / "AGENT" / "SETTINGS"` returns page instance class (type != CTkLabel; no more placeholder).
- TR (rubric 0-2 pass >=1.5): Minimal diff size for shell — <= 40 lines added total.

**Dependencies:** Task 1 done (pages will import v4_design primitives — but task 2 shell only references import/class names; not internals so pages don't need to exist yet; but do after pages; wait, Task 2 shell import pages; if pages don't exist yet import fails; so actually order Task 2 AFTER pages write OR Task 2 registry dict can have lazy import; but easier: implement Task 1, implement Task 3-12 pages THEN update app_shell registry mount (Task 13). Adjust dependency — actually lets schedule this Task 13 after pages done; registry mount last.

**Dependencies:** Pages 3-12 completed (to avoid ImportError). Update to Task 13 slot.

---

### Task 3: HOME — Keep existing premium layout; confirm clipping fix, semantic mapping preserved
**Status:** pending
**Priority:** high
**Maps ACs:** F3 (rubric >=1.5), F14 (rule data integrity), F15 (rule animation safety).

**Implementation plan:**
1. File: `src/hermes/ui/v4_home.py` (existing; already 2 bug-fix passes done).
2. Verify: System Status 6 rows visible at 1200x800 (no vertical clipping); if still clipped adjust inter-section pad more.
3. Verify semantic mappings from audit: AGENT=phase, APPROVAL=approval_state, VERIFY=verification_state, RECOVERY=recovery_state, CONNECTION=NOT REPORTED, SESSION=EVENTS · n.
4. Animation lifecycle (destroy, __del__, after_cancel) confirmed intact.
5. Refactor hero/status tile row to optionally use V4StatusTile primitive from v4_design if pattern matches exactly (optional; if causes regression skip).

**Test Requirements (TR):**
- TR (rule): test_v4_home 4/4 existing tests pass unchanged.
- TR (rubric >=1.5 / F3): SYSTEM STATUS rows at 1200x800 ALL 6 rows values visible; NOT clipped; Core 370px remains; Core breathing + orbital animation.
- TR (rule / F14 data integrity): grep — no `event_count → ONLINE/STREAMING`, no `verification_state → AGENT STANDBY/VERIFIED` strings remain.

**Dependencies:** Task 1 (v4_design exists).

---

### Task 4: NEW PAGE — Chat Screen (`v4_chat.py`)
**Status:** pending
**Priority:** high
**Maps ACs:** F4 (rule), F2 (rubric uses at least 4 v4_design primitives).

**Implementation plan:**
1. Create `src/hermes/ui/v4_chat.py` (NEW FILE). Pattern same as V4Home: (parent, store) __init__, store subscriber, destroy/__del__ for any timers; no store mutation.
2. Layout: 3 row: (row 0) V4PageHeader "CHAT" + online dot + phase status; (row 1) Scrollable CTkScrollableFrame = Conversation area (messages user/AI bubble left/right alignment; no fake messages; only real store events rendered + synthetic "SYSTEM READY" if zero). Real events map: reasoning_started = "🧠 Thinking…" muted badge; action_started = "▶ Action" badge; tool_called = "🛠 Tool: X" blue; observation = "👁 Observation" muted cyan; task_completed = "✓ Finished" green. If store does not expose actual chat user/user messages → show honest empty message area or Hermes activity timeline (do NOT fabricate conversation messages with user input).
3. Composer (row 2) = CTkEntry multi textarea + mic button + send button (button callable hooks: on_send / on_mic — informational only if no backend; no fake chat send function backend. Add comment integration placeholders).
4. Use primitives: V4Panel, V4PageHeader, V4SectionHeader, V4EmptyState, V4StatusBadge (≥4).

**Test Requirements (TR):**
- TR (rule): Instantiate without ctk (headless test: CTkFrame mock parent; no exception). Empty state renders when event_count=0: "NO CONVERSATION YET" or honest empty.
- TR (rule): No fake chat messages — for count=0, no fabricated "user said X".

**Dependencies:** Task 1.

---

### Task 5: NEW PAGE — Agent Live Execution Center (`v4_agent.py`)
**Status:** pending
**Priority:** high
**Maps ACs:** F5 (rule).

**Implementation plan:**
1. Create `src/hermes/ui/v4_agent.py` — same pattern (parent, store).
2. V4PageHeader "AGENT" + large phase badge center top.
3. Upper: big phase status (ONLINE/IDLE / EXECUTING / AWAITING APPROVAL / FAULT gold/magenta etc).
4. Middle: 2-col grid MISSION · PHASE · CURRENT STEP · ACTION · OBSERVATION · VERIFY · APPROVAL · RECOVERY tiles. Fields come from snapshot or store._events latest. Only show fields that exist in contract; else "—".
5. Bottom: last 5 events timeline (subset of recent activity).
6. Awaiting approval (approval=required) gold GLOBAL banner + APPROVE / DENY buttons (hook only: do not duplicate V3 approval engine; just call integration hooks — backend's canonical approval_dialog still authority.

**Test Requirements (TR):**
- TR (rule): Idle empty renders "SYSTEM READY".
- TR (rule): When snapshot approval_state=required — approve/deny buttons visible; button hook does NOT create second approval engine.

**Dependencies:** Task 1.

---

### Task 6: NEW PAGE — Memory Control Center (`v4_memory.py`)
**Status:** pending
**Priority:** medium
**Maps ACs:** F6 (rule).

**Implementation plan:**
1. Create `src/hermes/ui/v4_memory.py`.
2. 2x2 grid of 4 memory sections: SESSION, EPISODIC, LONG-TERM, CONTEXT.
3. No public read-only listing APIs in backend memory modules exist (per audit) → each shows HONEST NO DATA with explanation: "Memory data access is gated by the runtime MemoryPolicy layer and is not directly surfaced to the UI. Entries are stored on-disk at %LOCALAPPDATA%\\HermesClient\\state and audited via V3 test_secret_scrubbing_v3."
4. NEVER fabricate session IDs, tokens, passwords, credentials or any secret-like data even as placeholder.

**Test Requirements (TR):**
- TR (rule): No strings "password/token/secret/credential/apikey/api_key" inside values (labels OK).
- TR (rule): Empty state for all 4 groups; no hardcoded memory entries.

**Dependencies:** Task 1.

---

### Task 7: NEW PAGE — Skills Library (`v4_skills.py`)
**Status:** pending
**Priority:** high
**Maps ACs:** F7 (rule).

**Implementation plan:**
1. Create `src/hermes/ui/v4_skills.py`.
2. __init__: call `skills.loader.load_all_skills()` → REAL `list[SkillHint]` for rendering. Wrap in try/except ImportError/OSError to be safe; empty list if fails.
3. Sections: LEARNED SKILLS (count = len(all skills)), RECENTLY USED (empty if recent usage meta from store events / if no events → empty placeholder), NEEDS REPAIR (empty — if validation/repair meta not in contract → honest "No reported skill repairs"), AVAILABLE (= all list with expandable cards showing: Title; Skill ID; Preferred tools list; Hints; Security level).
4. Each skill card = V4Card collapsible detail layout; grid 2-col responsive when width grows.

**Test Requirements (TR):**
- TR (rule): Instantiate page → uses `load_all_skills()` without crashing.
- TR (rule): NO sample skills hardcoded (never have "example_skill_1 / "Write report" sample). Real data when exists.

**Dependencies:** Task 1.

---

### Task 8: NEW PAGE — Computer/Windows Control Center (`v4_computer.py`)
**Status:** pending
**Priority:** medium
**Maps ACs:** F8 (rule).

**Implementation plan:**
1. Create `src/hermes/ui/v4_computer.py`.
2. Group tiles: SYSTEM, WINDOWS, PROCESSES, SERVICES, NETWORK, FILES, APPLICATIONS, DISPLAY&INPUT.
3. ONLY sources allowed for values: Python `platform.platform()`, `socket.gethostname()`, `sys.version`, Windows `ctypes.windll.kernel32.GetTickCount64()` → uptime; no psutil unless it is installed and import succeeds; NEVER fabricate CPU/RAM/Disk %s.
4. Values unavailable = NOT REPORTED.
5. No gaming HUD style, no charts.

**Test Requirements (TR):**
- TR (rule): No CPU/RAM/NETWORK hardcoded percentages.
- TR (rule): Hostname/OS/Python fields populated honestly from platform/socket/sys.

**Dependencies:** Task 1.

---

### Task 9: NEW PAGE — Browser Automation Control (`v4_browser.py`)
**Status:** pending
**Priority:** medium
**Maps ACs:** F9 (rule).

**Implementation plan:**
1. Create `src/hermes/ui/v4_browser.py`.
2. Layout: Top=V4PageHeader "BROWSER", bottom=6 state labels (OBSERVING / NAVIGATING / ACTING / WAITING / COMPLETED / FAILED — current UNKNOWN dot badge, and fields active page/URL/tab/title/screen state = NOT REPORTED).
3. If v4_store / projection does not expose browser state (confirmed audit: NO public canonical browser fields in 7 snap keys → honestly NOT REPORTED everywhere).

**Test Requirements (TR):**
- TR (rule): NO fake URLs/tab titles like "https://example.com / "Dashboard" anywhere.
- TR (rule): Page instantiates without error.

**Dependencies:** Task 1.

---

### Task 10: NEW PAGE — Tasks History (`v4_tasks.py`)
**Status:** pending
**Priority:** medium
**Maps ACs:** F10 (rule).

**Implementation plan:**
1. Create `src/hermes/ui/v4_tasks.py`.
2. 5 groups: CURRENT (single from store latest if executing), RECENT (last 3), COMPLETED, CANCELLED, FAILED.
3. Source data = store._events grouped by correlation_id/task_id (contract exposes these via UIEvent). If no tasks yet → empty state for each group.
4. Modern compact list/cards; not huge table.

**Test Requirements (TR):**
- TR (rule): event_count=0 → CURRENT empty "NO ACTIVE TASK".
- TR (rule): NO synthetic sample tasks (never hardcode "Deploy website").

**Dependencies:** Task 1.

---

### Task 11: NEW PAGE — Activity / Observability / Debug (`v4_activity.py`)
**Status:** pending
**Priority:** high
**Maps ACs:** F11 (rule).

**Implementation plan:**
1. Create `src/hermes/ui/v4_activity.py`.
2. Top filter chips: ALL / REASONING / ACTION / OBSERVATION / VERIFY / APPROVAL / RECOVERY / SUCCESS / FAILURE (filter on/off state, re-render filtered list on click).
3. Scrollable area: vertical timeline same visual taxonomy as HOME Recent Activity (8 event classes visually distinct per spec 8).
4. Event fields: timestamp (HH:MM:SS); category badge; title; detail; event_id; correlation_id; tool info if payload.
5. Expandable row if possible (detail); else wrap to show available fields honestly.

**Test Requirements (TR):**
- TR (rule): Store events = 10 → page renders max latest N.
- TR (rule): filter chips exist (at least 4 of 9); no TclError on click.

**Dependencies:** Task 1.

---

### Task 12: NEW PAGE — Settings Center (`v4_settings.py`)
**Status:** pending
**Priority:** high
**Maps ACs:** F12 (rule).

**Implementation plan:**
1. Create `src/hermes/ui/v4_settings.py`.
2. Real data: settings_store.load_settings_form() → SettingsFormData (api_url, api_key, model, prefer_short_responses, voice_enabled, wake_word_enabled, notifications_enabled).
3. 8 group sections: GENERAL (prefer_short_responses toggle/label), SERVER (api_url read-only text + API key masked input NEVER display raw string mask as `sk-•••••••` / `"●●●●●●●●"` — only masked save hook only), MODEL (model label/input if editable save), VOICE (voice_enabled checkbox), WAKE WORD (wake enabled), NOTIFICATIONS (notif enabled), UI (placeholder — if not in contract yet, honest "No UI preference settings exposed"), ADVANCED (raw YAML read-only text, redact masked secrets in yaml render — never show raw api_key password).
4. Save button → validate → save_settings_form() → success banner; if errors list error messages.

**Test Requirements (TR):**
- TR (rule): API key NEVER displayed raw as plaintext. Always masked.
- TR (rule): load_settings_form return → SERVER URL label populated with real value.

**Dependencies:** Task 1.

---

### Task 13: Update app_v4_shell.py (Task 2 moved here — page registry mount, minimal change)
**Status:** pending
**Priority:** high
**Maps ACs:** F1 (rule).

**Implementation plan:**
- See Task 2 details (registry + import + destroy lifecycle) → now safe to import all pages since pages written Task 4-12 done.

**Dependencies:** Tasks 3-12 completed (pages exist on disk).

---

### Task 14: V4 Test Suite Update / Add Pages Smoke & Integrity
**Status:** pending
**Priority:** high
**Maps ACs:** F17 (rule), F14 (rule), regression on V3 tests.

**Implementation plan:**
1. Existing: `tests/test_app_v4_shell.py` (4 tests — keep first 2 as-is, add 2 new: (a) nav click mounts real page class not placeholder CTkLabel; (b) iterate all 10 NAV_ITEMS — each _set_nav(item) then check content.winfo_children()[0] class name matches page class.
2. Existing: `tests/test_v4_home.py` (no modification unless breaks).
3. Add NEW: `tests/test_v4_pages_smoke.py` — 1 test per page = instantiate headless parent, empty store, assert no exception, empty state label text (SYSTEM READY / NOT REPORTED / NO DATA variants per page). Also 1 test = page instance __init__ calls store.get_snapshot() NEVER store.apply() / mutate / state (audit: store has mock.called not includes apply).
4. Run full pytest suite test_* to confirm no regression: especially test_settings_store 29/29, test_memory tests, test_mission tests, test_security, test_v4 tests.

**Test Requirements (TR):**
- TR (rule): test_app_v4_shell 4 tests ALL pass (including nav page class mount, not CTkLabel "V4 SHELL • CHAT").
- TR (rule): test_v4_pages_smoke all pass (9 new pages instantiate).
- TR (rule): pytest overall all V4 related tests pass, no regression.

**Dependencies:** Tasks 1, 3-13.

---

### Task 15: Runtime Real GUI Visual Validation (3 resolutions)
**Status:** pending
**Priority:** high
**Maps ACs:** F18 (rule), F16 (rubric responsive >=1.5).

**Implementation plan:**
1. Run: `$env:PYTHONPATH="$PWD\src"` + `python src\hermes\ui\app_v4_shell.py`.
2. Open window at 3 different resolutions: geometry "1200x800" (default already), then manually "1366x768", then "1920x1080" (set geometry in app before mainloop for test; or launch and resize).
3. Navigate ALL 10 pages in each size, observe:
   - overlap/clipping of text/widgets
   - section horizontal scroll / collapse
   - System Status rows at Home visible fully (clipped? y/n)
   - Recent Activity empty/activity cards fully visible
   - Scroll frames not collapsing content
   - Tk/Tcl errors? stderr output
4. Record evidence per resolution in final output description (list pages checked + result any issues found if any — and fixes before final report).

**Test Requirements (TR):**
- TR (rule): No Python/Tk crash, no TclError in stderr during navigation.
- TR (rule): At smallest 1366x768 — no clipped widget borders / text overlap; either weight redistributes or scrolls appear.
- TR (rubric 0-2 pass >=1.5 responsive): overall 1200x800 baseline dense balanced, 1920x1080 no over-spaced empty huge area.

**Dependencies:** Tasks 1-14 complete.

---

### Task 16: Independent Review Pass (AC Compliance Audit)
**Status:** pending
**Priority:** high
**Maps ACs:** ALL (review).

**Implementation plan:**
1. Code review full 10 page files:
   - (a) F14 DATA INTEGRITY — grep for any fabricated strings "ONLINE" from count; "ACTIVE" session; "STANDBY" verify; fabricated 25% progress; fake 99% CPU.
   - (b) F2 single design system usage — each page uses >=3 primitives from v4_design module.
   - (c) F15 ANIMATION lifecycle destroy/__del__/after_cancel pattern: every class with timers implements destroy/__del__.
   - (d) F3 HOME: Core size 370 not shrunk below 340.
   - (e) F6 MEMORY: NO secrets/plaintext credentials displayed.
   - (f) F12 SETTINGS: API key always masked.
   - (g) F18 visual: no hardcoded absolute place x/y >=100 screen pixels.
   - (h) Grep NO `rgba(` anywhere in new/modified files.
2. List every failed item as REMEDIATION TASKS pending (if none pass).

**Dependencies:** All prior 1-15 completed (including runtime fixes).

---

### Task 17: Final Report (User Output)
**Status:** pending
**Priority:** high

**Implementation plan:**
1. Compile report items exactly per user Section 26 (Sonuç Raporu) headings 1-9:
   1. Changed files list (absolute paths)
   2. V4 design component list (from v4_design module + usage count)
   3. All 10 page states: what each page shows honestly; what data used per page
   4. Test results summary: pytest counts pass/fail
   5. Runtime GUI test results per resolution
   6. Responsive test results per 3 resolutions + 2 optional middle (1440/1600).
   7. Unavailable fields list (per page UNKNOWN/NOT REPORTED items with reason: e.g. "Browser state not in V4 snapshot contract" / "Connection transport state not published" etc.)
   8. Remaining known issues (if any)
   9. Backend changes required if more data is needed (list backend features that would need to populate honestly unavailable — e.g. "Expose session/connection state snapshot keys: transport_state, session_id, connected_since" etc.)

**Dependencies:** All 1-16.

---

## Acceptance Coverage Map (SPEC AC → Tasks)

| AC | Covered by Tasks |
|---|---|
| F1 (Shell/NAV_ITEMS unchanged + 9 page registry) | Task 13 + Task 14 tests |
| F2 (Design System 8 primitives) | Task 1 + each page 4-12 (≥3 uses) |
| F3 (HOME — premium, no clipping, semantics OK) | Task 3 |
| F4 (Chat timeline/composer/empty) | Task 4 |
| F5 (Agent live execution: approval/phase/step — idle SYSTEM READY) | Task 5 |
| F6 (Memory 4 groups, NO secrets, honest empty) | Task 6 |
| F7 (Skills real load_all_skills, no samples) | Task 7 |
| F8 (Computer real Python/Windows API data only, no fake CPU/RAM) | Task 8 |
| F9 (Browser not_reported honest UNKNOWN, NO fake URL) | Task 9 |
| F10 (Tasks history, NO synthetic tasks) | Task 10 |
| F11 (Activity observability, event stream, filters) | Task 11 |
| F12 (Settings: load_settings_form, save validate, API key masked) | Task 12 |
| F13 (Approval canonical dialog single source; no duplicate) | Task 5 (hooks only) + check task 16 |
| F14 (Data integrity semantic mappings) | Task 3 (Home) + Tasks 4-12 (page audits) + Task 16 grep audit |
| F15 (Animation safety destroy/after_cancel) | Task 3 (Home destroy) + new pages (destroy if timers) + Task 16 |
| F16 (Responsive 5 sizes) | Task 15 (3 sizes + 2 additional optional) |
| F17 (V4 tests / app shell / pages smoke) | Task 14 |
| F18 (Real runtime GUI visual test 3 sizes × 10 pages) | Task 15 |
