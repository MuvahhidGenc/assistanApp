# SPEC: Hermes V4 — Complete Premium UI/UX Master Implementation

## Problem
Hermes V4 currently ships only a single HOME page plus 9 placeholder labels. The product must feel like the command center of an autonomous AI computer agent running on this machine, not a generic SaaS dashboard. The presentation layer must cover all 10 navigation screens with a consistent design system while rendering ONLY real backend/store data (zero fabrication).

## Users & Goals
| User | Goal |
|---|---|
| End-user (desktop owner) | Operate, observe and interact with the Hermes agent on this PC; diagnose, inspect history, configure settings |
| Developer / DevOps | Observe runtime state across 10 pages; inspect events; no synthetic state/metrics |
| Auditor | Confirm data integrity: every shown value traces to a real backend/store contract (no fabrication) |

## Non-Goals (explicitly not in scope)
- NO V3 runtime/orchestrator/reasoning/executor/security/memory/skill changes
- NO new event system, store, executor or duplicate backend logic
- NO fake backend feature additions (connection state, session state, browser state, memory listing, progress bars, CPU/RAM metrics) that the V3 contract does not already publish
- NO cyberpunk neon/gaming HUD style
- NO architectural changes to: `v4_store.py` · `v4_projection.py` · `v4_bridge.py` (read-only consumer only)
- NO Git operations (commit/push/reset/clean/revert performed by the agent — user performs these)

## Constraints
1. Valid Tk/CustomTkinter named hex colors ONLY. No `rgba()` strings.
2. Primary font Segoe UI; Consolas only for technical metadata (timestamp / event id / labels).
3. No hardcoded full-screen absolute positioning. Grid / pack with weights, minsizes, scrollable regions.
4. Animation `after()` callbacks MUST be cancelled on widget `destroy()`; no runaway timers; no post-destroy callbacks.
5. Page must render at: 1200x800, 1366x768, 1440x900, 1600x900, 1920x1080 without overlap/clipping.
6. UI = presentation & interaction only. NEVER mutate V3 canonical state from UI pages.
7. All 10 pages must share ONE design system (panels / section headers / status tiles / badges / timelines / empty states.
8. Backend = ONLY: `store.snapshot + ._events + `load_all_skills() + `load_settings_form()`.

## Functional Requirements (rule/rubric mix)

### F1. Shell / Global Shell (rule)
NAV_ITEMS constant MUST remain `["HOME","CHAT","AGENT","MEMORY","SKILLS","COMPUTER","BROWSER","TASKS","ACTIVITY","SETTINGS"]` (unchanged). Every nav item mounts its dedicated page (no placeholder label fallback for other 9 pages.
Evidence: pytest AST test `test_navigation_constant_exists_and_ordered still passes.

### F2. Single Design System (rule)
Reusable UI primitives exist named: `V4Panel`, `V4PageHeader`, `V4SectionHeader`, `V4StatusBadge`, `V4StatusTile`, `V4TimelineEvent`, `V4EmptyState`, `V4Card`. Every page uses ≥ 1 of these to prove shared palette/consistent look. Primitive module = valid Python classes/functions in a single reusable module; no duplicated palette strings spread in 10 pages.

### F3. Home Screen (rubric, 0-2 threshold >=1.5)
- Retain 370px Hermes Core; 2-col hero; 6 status tiles (hero cluster; Current Mission; System Status 6 rows; Recent Activity timeline. Status cluster rows never clipped at 1200x800. Semantic correctness (AGENT=phase not verify_state; CONNECTION/Session/Not reported if unavailable; SESSION shows event count not ACTIVE label; etc.) pass audit. Score: >=1.5.

### F4. Chat Screen (rule)
Modern conversation timeline: user/Hermes bubbles; thinking/executing state badges; composer text input + mic + send buttons; uses real `store._events` to render Hermes activity (no fake messages). Zero-events: honest empty state; no overlap; responsive wraplength auto-composer.

### F5. Agent Screen (rule)
Live execution center using snapshot phase + approval state + recent step/events from store events. Idle state = SYSTEM READY. Awaiting approval rendered with GOLD chip. Active step visually dominant when executing. Render MISSION / PHASE / CURRENT STEP / ACTION / OBSERVATION / VERIFY / APPROVAL / RECOVERY grid if fields exist (otherwise honest "—").

### F6. Memory Screen (rule)
4 groups: SESSION / EPISODIC / LONG-TERM / CONTEXT. No raw secrets. If no read-only public listing API not published by backend NOT available → honest NO MEMORY DATA state with explanation "Memory data is audited by policy and not directly surfaced to UI." No fabricated entries.

### F7. Skills Screen (rule)
Groups Learned / Recently Used / Needs Repair / Available. Loads REAL `loader.load_all_skills() (`SkillHint`). Shows: skill_id,title, preferred_tools, hints, security_level. Uses real skill data; never hardcoded sample skills. Skills groups empty state "NO SKILLS LOADED — Create reusable YAML skills in hermes/skills/procedures to add."

### F8. Computer Screen (rule)
Groups SYSTEM / WINDOWS / PROCESSES / SERVICES / NETWORK / FILES / APPLICATIONS / DISPLAY&INPUT. ONLY values sourced from standard Python libraries (platform, socket, ctypes uptime on Windows, sys) WITHOUT fabricating CPU/RAM/network. Unavailable metrics marked NOT REPORTED. No gaming performance HUD look.

### F9. Browser Screen (rule)
Active page, URL, title, current tab, automation state. State labels: OBSERVING / NAVIGATING / ACTING / WAITING / COMPLETED / FAILED. If real backend contract unavailable → HONEST NOT REPORTED / UNKNOWN state. NO FAKE URLs / TABS / titles.

### F10. Tasks Screen (rule)
Groups CURRENT / RECENT / COMPLETED / CANCELLED / FAILED. Items title/state/timestamp/phase/outcome from real events store events. No synthetic tasks. Zero = "NO ACTIVE TASK empty state.

### F11. Activity Screen (rule)
Detailed observability / debug screen: full `store._events` stream. 8 event categories visually distinct. Filter chips ALL / REASONING / ACTION / OBSERVATION / VERIFY / APPROVAL / RECOVERY / SUCCESS / FAILURE (all optional; if filters implemented they toggle. Scrollable list. Each event: timestamp; category badge; title; detail; event_id; correlation if any. Honest; never exceeds reasonable count.

### F12. Settings Screen (rule)
8 group GENERAL / SERVER / MODEL / VOICE / WAKE WORD / NOTIFICATIONS / UI / ADVANCED. Uses real `settings_store.load_settings_form()` display / model / voice / wake / notifications / prefer_short_responses; raw YAML read-only advanced view (without secrets). No fake toggle UI for settings not saved; only items backed by contract. Write-through: call save_settings_form() when user presses Save; if invalid show form validate errors).

### F13. Approval UI (integrity rule)
Approval-gated operations continue single canonical approval UI (approval_dialog unchanged; pages reference). AGENT screen awaiting_approval state gold banner + approve/deny buttons hooks (no duplicate approval engine added; no second approval logic).

### F14. Data Integrity (rule)
Audit trace: every shown value maps 1 real field / dataset:
- AGENT= phase
- APPROVAL=approval_state
- VERIFY=verification_state
- RECOVERY=recovery_state
- CONNECTION/SOCKET/BROWSER/CPU/RAM etc = NOT REPORTED if canonical field missing
- event_count never interpreted as connection/session state

Violations blocked. All pages read only. No store mutation from UI.

### F15. Animation Safety (rule)
Every class that uses `after()` MUST implement `destroy()` + `__del__` that calls `after_cancel()`. No duplicate `after()` after destroy. Runaway timers. No exceptions.

### F16. Responsive (rubric scale 0-2 pass >=1.5 score >
Tested 5 resolutions. 1366x768 minimum size min core; 1920x1080 largest. overlap/clipping/layout collapse. No clipped text. Page contents overlap never. 1366x768 COMPUTER and MEMORY grids MUST be scrollable if content height is dense; 1920x1080 whitespace balanced no gaps.

### F17. Test Suite (rule)
Test all 10 pages smoke test (page instantiation without exception; page imports; empty state state state render; store subscribe event propagates; no state mutation; no duplicate events system. Updated test_app_v4_shell.py: 9 new page mounts when nav _set_nav called per item returns page page widget instance not CTkLabel "V4 SHELL • placeholder. All tests pass: test_v4_home existing. No V3 test regression (no test_settings_store, test_v4 tests pass same).

### F18. Real Runtime GUI Validation (rule — not 4-second alive; real window actually inspected real window at: 1200x800 1366x768 1920x1080 10 navigation pages visual: clipping / overlap / empty space / unreadable text / scroll problems / layout collapse / animation Tk Tcl errors. Must be checked visually.

## Dependencies (real modules already in repo:
- `modern_theme.py palette; `v4_store.py`, `v4_home.py`, modern_theme.
- `settings_store.load_settings_form` / `SkillHint.load_all_skills` from existing hermes.skills.loader
- No new runtime contract;  V4 pages presentation.

Assumptions:
- `app_v4_shell.py` can be minimally modified only to swap placeholder mount line CTkLabel • {page mount (import) → page class instantiation); V4 AppShell import changes.
- No runtime modules touch (no orchestrator, reasoning, executor, memory, config, policy; changes.
- Shell import of `settings store public APIs are read import only (no state mutation)
Open Questions: (user asked for full 10 pages immediately; no waiting.
