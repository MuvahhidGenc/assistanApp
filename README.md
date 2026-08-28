# HERMES AI — Windows Client

Windows üzerinde çalışan kişisel IT Assistant istemcisi. AI reasoning ve agent zekası **Hermes Server** tarafında çalışır; bu uygulama sunucunun API client'ıdır.

## Mimari

```
┌─────────────────────────────────────────────────────────────┐
│                    Windows Client (bu proje)                 │
│  ┌─────────┐  ┌──────────┐  ┌─────────┐  ┌──────────────┐  │
│  │ UI/Voice│  │  Agent   │  │ Security│  │ Local Tools  │  │
│  │ (Phase) │→ │Orchestr. │→ │ Policy  │→ │  Executor    │  │
│  └─────────┘  └────┬─────┘  │ Approval│  └──────────────┘  │
│                    │        │ Audit   │                     │
│                    ▼        └─────────┘                     │
│              Hermes Server Client (HTTP/S)                  │
└────────────────────────────┬────────────────────────────────┘
                             │ HTTPS
                             ▼
┌─────────────────────────────────────────────────────────────┐
│                     Hermes Server                            │
│   LLM · Planning · Reasoning · Sessions · Runs · Approvals  │
└─────────────────────────────────────────────────────────────┘
```

### Temel ilke

AI doğrudan işletim sistemi komutu **çalıştırmaz**. Tüm işlemler şu zincirden geçer:

```
Server Tool Request → Local Policy Engine → User Approval → Windows Executor → Audit Log
```

## Geliştirme aşamaları

| Aşama | Durum | Açıklama |
|-------|-------|----------|
| 1. Temel Hermes + LLM | ✅ | Server client, agent orchestrator, CLI |
| 2. Voice + Wake Word | 🔲 | Ses modülü iskeleti hazır |
| 3. Agent + Planning | 🔲 | Server-side planlama entegrasyonu |
| 4. Approval + Security | ✅ | Policy engine, approval manager, audit |
| 5. Windows Tools | ✅ | Sistem, network, servis, event log, registry (read) |
| 6. Computer Control | 🔲 | Mouse, keyboard, screenshot |
| 7. Vision | 🔲 | Ekran analizi |
| 8. Browser | 🔲 | Chrome, Edge, Firefox |
| 9. IT Tools | 🔲 | IT Mode araç seti |
| 10. Diagnostic | 🔲 | Sorun giderme akışları |
| 11. Memory | 🔲 | Konuşma/görev hafızası |
| 12. PC Deployment | 🔲 | Corporate/Machine Profile |
| 13. Modern UI | 🔲 | System tray, chat arayüzü |
| 14. Security Hardening | 🔲 | Ek güvenlik katmanları |

## İki farklı `hermes` komutu

| Komut | Nerede | Ne yapar |
|-------|--------|----------|
| `hermes status` | **Sunucu** (Linux) | Gateway, provider, auth, docker durumu |
| `hermes chat` | **Sunucu** (Linux) | Sunucu üzerinde doğrudan agent ile konuşma |
| `hermes-client status` | **Windows** | API bağlantı testi (`GET /v1/models`) |
| `hermes-client chat` | **Windows** | API üzerinden agent + yerel tool/policy katmanı |

Sunucuda `hermes chat` zaten çalışıyorsa API erişimi tamamdır. Windows client, buna ek olarak ses, UI, mouse/keyboard, IT tool'ları ve güvenlik katmanını sağlar.

## Kurulum

```powershell
cd D:\calismalarim-2\assistan
.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
```

## Yapılandırma

```powershell
hermes-client configure
```

- **Server URL** → `.env` dosyasına yazılır (`http://50.6.226.228:8642`)
- **API Key** → Windows Credential Manager'a kaydedilir
- **Model** → `hermes-agent` (API'deki model adı; sunucu iç modeli gpt-5.5 olabilir)

## Kullanım

```powershell
# Windows client API bağlantı testi
hermes-client status

# Yerel Windows tool listesi
hermes-client tools

# API üzerinden mesaj gönder
hermes-client chat "Bu bilgisayarin sistem bilgilerini goster"

# İnteraktif oturum
hermes-client interactive
```

## Proje yapısı

```
src/hermes/
├── main.py              # CLI giriş noktası
├── app/                 # Uygulama bootstrap
├── config/              # Ayarlar ve credential yönetimi
├── server/              # Hermes Server HTTP client
├── agent/               # Client-side agent orchestrator
├── security/            # Policy, approval, audit
├── tools/               # Yerel Windows tool sistemi
├── voice/               # (Phase 2)
├── ui/                  # (Phase 13)
└── memory/              # (Phase 11)
```

## Güvenlik

- Tool risk seviyeleri: `read_only`, `normal_modification`, `high_risk`
- Kritik işlemler kullanıcı onayı olmadan çalışmaz
- Yasaklı tool kalıpları (format_disk, registry_delete vb.) her zaman reddedilir
- Tüm işlemler audit log'a kaydedilir; şifre/token/api_key redakte edilir
- API key kaynak kodunda veya `.env`'de tutulmaz (Credential Manager)

## Test

```powershell
pytest
```

## Hermes Server API

Client base URL format: `http://host:8642/v1` (version prefix included).

Primary connectivity test:

```
GET /models
```

Other endpoints (relative to base URL):

- `GET /models` — list models (connectivity test)
- `GET /capabilities` — server capabilities (optional)
- `POST /sessions` — create session
- `POST /sessions/{id}/runs` — start agent run
- `GET /runs/{id}/events` — SSE streaming
- `POST /runs/{id}/stop` — stop run
- `POST /runs/{id}/steer` — steer run
- `POST /runs/{id}/tool_results` — submit local tool results
- `POST /approvals/{id}` — submit approval
- `POST /chat/completions` — chat (OpenAI-compatible)

Configuration:

```powershell
hermes configure
# Server URL: http://50.6.226.228:8642/v1
# Model: hermes-agent
```
