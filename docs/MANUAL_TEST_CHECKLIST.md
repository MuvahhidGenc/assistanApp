# HERMES — Manuel Test Kontrol Listesi

Her phase sonrası bu listeyi kullanın. Exe derlemeden önce otomatik testler geçmeli:

```powershell
cd D:\calismalarim-2\assistan
.\.venv\Scripts\python.exe -m pytest tests/ -q
```

Exe derleme:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\package_windows.ps1
```

**Çıktılar:**
- `dist\hermes-client.exe`
- `release\HermesClient\hermes-client.exe`
- `release\HermesClient.zip`
- `%LOCALAPPDATA%\HermesClient\hermes-client.exe` (Hermes kapalıysa kopyalanır)

---

## 0. Kurulum / Başlatma

1. Görev Yöneticisi'nde eski `hermes-client.exe` süreçlerini kapatın.
2. `release\HermesClient\hermes-client.exe` çalıştırın (veya zip’ten açın).
3. Tepsi simgesi görünmeli; çift tıklayınca sci-fi HUD (1360×820) açılmalı.
4. Settings → API URL + Token kaydedin → bağlantı durumu yeşile dönmeli.

**Log:** `%LOCALAPPDATA%\HermesClient\logs\app.log`

---

## 1. Fast path (PHASE 2–3 — değişmemeli)

| Komut | Beklenen |
|-------|----------|
| `dns degistir google yap` | Hızlı yerel DNS; sunucu Runs API çağrılmadan sonuç |
| `chrome ac` | Tarayıcı açılır |
| `ekran goruntusu al` | Screenshot tool |

Mission **oluşturulmamalı** (basit komutlar).

---

## 2. Mission + Planner (PHASE 3)

| Komut | Beklenen |
|-------|----------|
| `Standart PC kurulumu yap` | Mission başlar; plan oluşur; adımlar chat’te görünür |
| Uygulama kapat/aç | `%LOCALAPPDATA%\HermesClient\state\missions\` altında mission JSON kalır |

Mission dosyası: `state\missions\{id}.json` — `plan_validated`, `steps` dolu olmalı.

---

## 3. Verify (PHASE 4)

Mission adımlarında tool `success` dönse bile verify fail ise adım tamamlanmamalı.

**Kontrol:** Mission JSON’da step alanları:
- `verification_status`: `verified` / `failed` / `unknown`
- `observation`: boş olmamalı (tool output kopyası değil, ayrı snapshot)

---

## 4. Recovery (PHASE 5)

| Senaryo | Nasıl dene | Beklenen |
|---------|------------|----------|
| Retry | Ağ/kısa kesinti sonrası mission devam | Chat’te “Tekrar deniyorum” benzeri mesaj |
| Zaten kurulu | Chrome zaten yüklüyken kurulum mission’ı | “Zaten kurulu, doğruluyorum” → step complete |
| Non-fatal | Opsiyonel program (Telegram vb.) başarısız | Mission devam; step `skipped` |
| Fatal | DNS / kritik adım fail | Mission durur veya `waiting_for_user` |
| Auth | Private GitHub repo clone | “Authentication gerekiyor” → mission bekler |

**Mission state:**
- `recovery_attempts[]` dolu
- `recovery_strategy`, `recovery_reason` kayıtlı
- `waiting_for_user` durumunda `waiting_for_user_reason` dolu

**Resume:** Uygulama kapat/aç → aktif mission tepsi/HUD’da görünür olmalı.

---

## 5. Security (her phase)

| Komut | Beklenen |
|-------|----------|
| Riskli tool (silme, format vb.) | Onay penceresi / “Onay bekleniyor” |
| Onay vermeden | İşlem yapılmamalı |

Audit: `%LOCALAPPDATA%\HERMES\audit.log`

---

## 6. Regresyon — hızlı smoke (5 dk)

- [ ] Exe açılıyor, HUD görünüyor
- [ ] Settings kaydediliyor
- [ ] Basit DNS komutu çalışıyor
- [ ] Karmaşık mission başlıyor (PC kurulumu)
- [ ] Log dosyasında hata yok (auth/network hariç)
- [ ] pytest: 253 passed (geliştirici makinesinde)

---

## Sorun giderme

| Belirti | Olası neden |
|---------|-------------|
| Eski UI | Eski exe çalışıyor — `release\HermesClient\` exe kullanın |
| Bağlantı yok | VPS erişilemiyor veya yanlış API token |
| LOCAL_TOOL bağlı değil | Sunucu tarafı; client RPC 127.0.0.1:8765 |
| Exe kopyalanmadı | Hermes açık — kapatıp build tekrar |

---

Son build: PHASE 5 (Recovery Engine) — 253 test.
