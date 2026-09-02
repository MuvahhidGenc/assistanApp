# HERMES Agent Core V2 — Uygulama Planı

Bu belge onaylanmış mimari kuralları ve faz bazlı uygulama planını içerir.

## Mimari Kurallar (Onaylı)

### 1. Mission seçimi yalnızca mevcut heuristic'lere bağlı değil

`should_defer_to_server()` ve benzeri fonksiyonlar korunur; Mission oluşturma kararı **yalnızca** bunlara bağlı değildir.

| Örnek | Yol |
|-------|-----|
| "DNS'i Google DNS yap" | Fast path |
| "Standart PC kurulumu yap" | Mission |
| "Bu GitHub reposunu analiz et, kur ve çalıştır" | Mission |
| "Bu bilgisayarı analiz et ve güvenli şekilde optimize et" | Mission |
| "Bu siteyi favoriye ekle ve masaüstüne uygulama olarak oluştur" | Mission |

Basit görevler mevcut fast path'i kullanır. Hedefi olan, çok adımlı, belirsiz, araştırma gerektiren veya birden fazla tool/agent gerektiren görevler Mission Engine'e girebilir.

**PHASE 2:** `mission/selection.py` — `is_fast_path_candidate()`, `should_create_mission()`, `should_route_to_mission()`

### 2. AI maliyeti minimum

- Planner Mission başında mümkün olduğunca **tek seferde** plan oluşturur.
- Her tool/action öncesinde LLM çağrısı yapılmaz.
- Öncelik: Python / Windows API / PowerShell / Playwright / Git / OCR / mevcut tool'lar.
- AI yalnızca: başlangıç planlama, belirsiz karar, hata analizi, recovery, kullanıcı talimatı değişikliği, gerçekten gerekli reasoning.
- Mission Engine aynı sonucu tekrar tekrar AI'ya sormaz.
- Provider abstraction ileride farklı model/provider eklenmesine izin verir.

**PHASE 2:** LLM çağrısı yok — yalnızca state/persistence.

### 3. Mission state Memory ile uyumlu

Mission state gelecekte working / episodic / long-term memory ile bağlanabilecek şekilde tasarlanır:

- `schema_version` ile versioned persistence
- `memory_refs`, `working_context`, `observations`, `important_decisions` alanları
- Geçici Python objesi değil; disk üzerinde extensible JSON

**PHASE 2:** `mission/models.py`, `mission/store.py`

### 4. Skills hardcoded workflow değil

YAML procedure dosyaları sabit sıraya zorlamaz. Skill tanımlar: amaç, varsayılan tercihler, önerilen araçlar, kontroller, güvenlik seviyesi, doğrulama kuralları, opsiyonel adımlar.

Nihai plan: **SYSTEM STATE + MEMORY + SKILL + USER GOAL** birleşimi.

**PHASE 10** — PHASE 2'de skill yok.

### 5. Observe katmanı Vision-ready

PHASE 4'te abstraction genişletilebilir olmalı:

```
UI Automation / Browser DOM → OCR → local observation → optional Vision AI
```

**PHASE 11** Vision AI — PHASE 2'de observe yok.

### 6. Security asla bypass edilmez

```
Mission Engine → ToolRegistry → ToolExecutor → PolicyEngine → ApprovalManager → Audit
```

Mission Engine doğrudan subprocess/PowerShell/mouse işlemi yapmaz.

**PHASE 2:** Mission yalnızca state tutar; tool execution mevcut orchestrator/executor üzerinden devam eder.

### 7. PHASE 2 kapsamı küçük

PHASE 2 kapsamı:

- `Mission`, `MissionStep`, `MissionStatus`
- persistence, resume, serialization
- basic UI state
- testler

**Henüz yok:** Browser Agent, GitHub Agent, Vision, Obsidian, Parallel Execution, büyük orchestrator refactor.

### 8. Geriye dönük uyumluluk

Mevcut 201 test korunur. PHASE 2 sonunda:

- [x] Fast path çalışıyor
- [x] Local tools çalışıyor
- [x] Security çalışıyor
- [x] RPC çalışıyor
- [x] Mission oluşturulabiliyor
- [x] Mission persist edilebiliyor
- [x] Uygulama kapanıp açıldığında state okunabiliyor
- [x] Mission resume edilebiliyor

---

## Faz Özeti

| Faz | Kapsam |
|-----|--------|
| 1 | Analiz + plan (tamamlandı) |
| **2** | **Mission state + persistence + resume + UI snapshot + testler** |
| **3** | **AI Planner — tek seferlik planlama, validation, engine execution** |
| **4** | **Observe/Verify — EXECUTE → OBSERVE → VERIFY pipeline** |
| 5 | Recovery engine |
| 6–8 | Browser, GitHub, Install agents |
| 9 | Memory (Obsidian, episodic, long-term) |
| 10 | Skills (hint-based, not hardcoded) |
| 11 | Vision AI provider |
| 12 | Parallel execution + orchestrator evolution |

## PHASE 3 Dosyalar

```
src/hermes/mission/
├── context.py     # system state + compact tool manifest + planning prompt
├── planner.py     # tek AI çağrısı + heuristic fallback
├── validator.py   # plan schema validation
├── engine.py      # plan execution via ToolExecutor chain
└── ...

src/hermes/skills/
├── loader.py
└── procedures/standard_pc_setup.yaml

tests/test_mission_planner.py
```

## PHASE 3 Tamamlandı

- Tek seferlik AI planning (`ChatRequest stream=False`)
- Plan validation (tool, args, dependencies, risk)
- Heuristic fallback (AI timeout/failure/invalid plan)
- Cached plan resume (re-plan yok)
- Execution: MissionEngine → ToolExecutor → Policy → Approval → Audit
- 228 test geçiyor

## PHASE 4 Tamamlandı

```
src/hermes/tools/verifiers/
├── base.py        # VerificationStatus, Observation, VerificationResult
├── context.py     # VerifierContext + observe callback
├── registry.py    # VerifierRegistry
├── generic.py     # generic fallback
└── specific.py    # git_clone, install, dns, file, folder, open_url

tests/test_verifiers.py
```

- EXECUTE → OBSERVE → VERIFY döngüsü MissionEngine'de
- `success=True` ≠ `verified=True`
- Verification failure → `verification_failed` + `recovery_attempts`
- Observe: read-only tool'lar ToolExecutor üzerinden
- 243 test geçiyor

## PHASE 5 Tamamlandı

```
src/hermes/mission/recovery/
├── config.py      # RecoveryConfig (budget, backoff)
├── classifier.py  # ErrorCategory
├── context.py     # RecoveryContext, idempotency
├── strategies.py  # RecoveryStrategy implementations
└── engine.py      # RecoveryEngine

tests/test_mission_recovery.py
```

- ERROR → CLASSIFY → RECOVER → VERIFY
- Deterministic recovery (no AI)
- waiting_for_user + resume_from_user
- fatal / non_fatal steps
- 253+ test geçiyor

---

## PHASE 2 Dosyalar

```
src/hermes/mission/
├── __init__.py
├── models.py      # Mission, MissionStep, MissionStatus
├── selection.py   # fast path vs mission routing
└── store.py       # persist, load, resume

docs/AGENT_CORE_V2_PLAN.md
tests/test_mission.py
```

State dizini: `%LOCALAPPDATA%\HermesClient\state\missions\`
