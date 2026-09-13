# MUVAHHİD V4 REDESIGN — GÖREV LİSTESİ

Kapsam: V4 UI presentation layer redesign; V3 runtime/orchestrator/security/approval/memory/skills DEĞİŞTİRİLMEYECEK.

Kabul kriter bağlantıları ( ←F# ): AC (spec.md) F numarası

---

## Task 1: MUVAHHİD Brand Entegrasyonu + Shell Header/Nav/Footer

**Kapsam:** `src/hermes/ui/app_v4_shell.py` + `src/hermes/ui/v4_design.py` brand constant'ları
**Öncelik:** Yüksek
**Ana Kriter:** ←F1, ←F2, ←F13, ←F14

### Uygulanacaklar

1. `app_v4_shell.py` içinde `_BRAND_TITLE = "MUVAHHİD V4"` + `_BRAND_SUB = "AI BİLGİSAYAR AJANI"` sabitleri; title `"MUVAHHİD V4"` (L70 önceki Hermes V4 değişecek).
2. Header satırı (L77-86 arası) yeniden:
   - SOL: M lettermark icon (canvak 24×24 küçük logo) + `MUVAHHİD V4 • AI BİLGİSAYAR AJANI`
   - ORTA3 grup sırası: `FAZ | {TURKCE_PHASE}` • `BAĞLANTI | {BİLİNMİYOR ya da gerçek varsa}` • `OLAY | {count}`
   - SAĞ: `strftime("%H:%M")` saati (Consolas, 12, BÜYÜK)
3. `NAV_ITEMS` ingilizce anahtar SABİT KALIR (←F1); ama her buton text'i Türkçe display:
   ```python
   _NAV_TR = {"HOME":"ANA SAYFA","CHAT":"SOHBET","AGENT":"AJAN","MEMORY":"HAFIZA",
             "SKILLS":"YETENEKLER","COMPUTER":"BİLGİSAYAR","BROWSER":"TARAYICI",
             "TASKS":"GÖREVLER","ACTIVITY":"AKTİVİTE","SETTINGS":"AYARLAR"}
   ```
4. Nav butonları style: Dar 150px sidebar (1920×1080'de 180px otomatik grid weight). Seçili buton: border_width=1 NEON_CYAN + fg_color=CARD (ince glow), hover renk BUTTON_HOVER. Büyük parlak neon buton YOK.
5. ←F2 (no Hermes string): `grep -ri` taranacak; import/NAV_ITEMS anahtarları hariç UI display string'lerinde Hermes YOK.

### Test Gereksinimleri (TR)

| ID | Tip | Durum | Kanıt |
|----|-----|-------|------|
| TR 1.1 | rule | Pass | `test_nav_items_display_labels_turkish` PASS |
| TR 1.2 | rule | Pass | `grep -i "hermes" | grep -v NAV_ITEMS | grep -v import | grep -v "NAV_TR\|PAGE_REGISTRY"` 0 satır |
| TR 1.3 | rubric (0-2, ≥1.5) | Puan 2 | 1200×800'de sidebar 150±2px; header 30px; footer 50px; oran referansa yakın |

### Bağımlılık: Yok.
### Durum: pending

---

## Task 2: Design System Yenileme — HUD Corner Motifleri + Ses-Reaktif Probe Interface

**Kapsam:** `src/hermes/ui/v4_design.py` + YENİ `src/hermes/ui/v4_audio.py`
**Öncesi:** Task 1 tamamlanmalı.
**Öncelik:** Yüksek
**Ana Kriter:** ←F3, ←F7, ←F8, ←F12

### Uygulanacaklar

#### v4_design.py eklemeleri:
1. `V4HUDPanel` sınıfı (V4Panel sub-class): 4 köşede L-şekli 6px küçük çizgi (Canvas ile; sol-alt, sol-üst, sağ-alt, sağ-üst; NEON_CYAN ince 1px; corner radius OFF). Bütün sayfalardaki ana wrapper bu sınıftan olacak.
2. `V4CoreCanvas` (widget sınıfı): Tk Canvas wrapper; state, audio amplitude, palette, size parametreleri alan public draw fonksiyonu. Bu sınıf HOME + AJAN sayfalarında kullanılabilir.
3. `V4HUDStatusChip` (inline status): FAZ / BAĞLANTI / OLAY için chip (icon dot + iki label: key / value).
4. Renk eşleştirmeleri ←F8 doğrultusunda: `_MUVA_PALETTE` dict.
5. TÜM Türkçe durum string'leri `_TR_PHASE = {"idle":"HAZIR","listen":"DİNLİYOR","think":"DÜŞÜNÜYOR","executing":"ÇALIŞIYOR","speaking":"KONUŞUYOR","approve":"ONAY BEKLİYOR","completed":"TAMAMLANDI","error":"HATA"}`.

#### v4_audio.py (YENİ):
1. `AudioProbe` sınıfı (read-only UI helper; backend store'a YAZMAZ — ←F12):
   - `__init__()`: Import-safe pyaudio/SpeechRecognition try/except; ImportError → `available=False`.
   - `start(sample_rate=16000, frames_per_buffer=512)`: Thread-safe stream aç; 30 FPS callback; RMS değeri 0.0–1.0 normalize et.
   - `stop()`: Stream kapat; thread join.
   - `get_amplitude()` → float 0.0–1.0; yoksa 0.0 döner.
   - `destroy()/__del__()`: after_cancel; stream close; try/except sessizce.
2. `SafeFallbackWaveform(seed: float) -> list[float]`: Gerçek amplitude olmadığında 0.3 Hz sakin dalga (sabit, tekrarlayan ama kilitlenme YOK).

### Test Gereksinimleri

| ID | Tip | Durum | Kanıt |
|----|-----|-------|------|
| TR 2.1 | rule | Pass | V4HUDPanel 4 corner line çizdiği Canvas testi (12x12 köşelerde 4 line count) |
| TR 2.2 | rule | Pass | test_no_rgba PASS; her renk 7+ digit hex |
| TR 2.3 | rule | Pass | AudioProbe destroy() çağrınca stream.is_stopped True veya ImportError safe |
| TR 2.4 | rule | Pass | AudioProbe herhangi bir V3 runtime modülü import etmez |
| TR 2.5 | rubric (0-2, ≥1.5) | Puan 2 | 7 durum renk paleti birbirinden bariz farklı; HAZIR sakin cyan, HATA magenta, ONAY gold, KONUŞUYOR green |

### Durum: pending

---

## Task 3: HOME Ana Sayfa — Referans Kompozisyon Uygulama

**Kapsam:** `src/hermes/ui/v4_home.py` tamamen rewrite (eski 2 kolonlu hero 3 kolonlu LEFT/CENTER/RIGHT + BOTTOM yap).
**Öncesi:** Task 2 tamamlanmalı.
**Öncelik:** KALDIRILMIYOR (en yüksek)
**Ana Kriter:** ←F6, ←F9, ←F10, ←F11, ←F12, ←F13, ←F16, ←F17, ←F18

### Uygulanacaklar

Layout grid ana hatları (4×3 ana grid):
```
Sol Sidebar (weight 0)  |  Center Core (weight 3)  |  Right Chat (weight 1)
                           BOTTOM Status Bar (weight 0, 5 tile)
```

1. **SOL SIDEBAR** (Task 1'deki NAV → wrapper V4HUDPanel, 150–180px genişlik, min boyut 150).
2. **CENTER** — V4HUDPanel içinde V4CoreCanvas (←F9, ←F10):
   - Çekirdek: Nükleus (çoklu çap artışı ile gradient simülasyonu, 15 px artış, alpha renk farkı)
   - 4+ orbital halka (2 tanesi TURUNCU partial segment, 2 tanesi FULL cyan, 1 tanesi dashed scan)
   - 12+ parçacık (halkanın çeşitli noktalarında küçük daire)
   - Yatay waveform (180 bar, amplitude audio_probe.get_amplitude() × 40 px)
   - Dış scan mark (30° aralıklarla küçük tick çizgileri)
   - M logosu (glow stroke + kalın yazı)
   - Halka + parçacık orbital motion animasyonu (60 FPS değil, 30 FPS performans için).
   - Altında metin: `MUVAHHİD` + 1 satır durum (`Sizi dinliyor...` / `HAZIR` / `ÇALIŞIYOR` ←F11 Türkçe).
3. **RIGHT CHAT PANEL** — V4HUDPanel (genişlik 300–360px responsive min 300):
   - Başlık satırı: SOHBET icon + text; sağda ses durumu icon + ayar icon.
   - Scrollable timeline: CTkScrollableFrame; User bubble sağ (dark cyan bg); MUVAHHİD bubble sol M logo small + dark bg.
   - 4 öneri chip: "Bilgisayarımı kontrol et / Bir web sayfası aç / Yeni bir görev başlat / Hafızamda ara" (buton stili minimal, click → şu an için metin alanına doldurur — entegrasyon hook).
   - Composer: attach icon + mesaj yazı alanı (CTkEntry çok satırlı veya CTkTextbox min 40px) + mic icon + send icon.
   - En altta micro durum satırı: mic icon + "Sesli komut hazır | Uyandırma kelimesi: MUVAHHİD".
   - ←F17: Fabricated mesaj YOK. Sadece 1 karşılama bubble "Merhaba! Ben Muvahhid. Size nasıl yardımcı olabilirim?" (1 kere init).
4. **BOTTOM STATUS BAR** — 5 V4HUDStatusChip (←F18):
   - AJAN (icon + HAZIR — phase→TR map)
   - ONAY (approval_state→GEREKMİYOR / GEREKLİ / BEKLİYOR)
   - DOĞRULAMA (verification_state→BEKLEMEDE / DOĞRULANDI / HATA)
   - KURTARMA (recovery_state→YOK / DEVAM EDİYOR / HATA)
   - HATA (error_state→YOK / HATA)

### Test Gereksinimleri

| ID | Tip | Durum | Kanıt |
|----|-----|-------|------|
| TR 3.1 | rule | Pass | 1200×800'de CENTER Core canvas ≥ 480px; 1920×1080 ≤ 720px |
| TR 3.2 | rule | Pass | Core canvas 200+ çizim nesnesi (id count ≥ 200) |
| TR 3.3 | rule | Pass | 4 öneri chip tam 4 adet; label doğru |
| TR 3.4 | rule | Pass | Uyandırma kelimesi METNİ "MUVAHHİD"; backend wake_word listesine dokunmadı (grep settings.py değişmedi) |
| TR 3.5 | rule | Pass | Bottom status: 5 ayrı status chip count 5 |
| TR 3.6 | rubric (0-2, ≥1.5) | Puan 2 | Referans görsel kompozisyon benzerliği: LEFT/CENTER/RIGHT oran %80; turuncu segmentler yerinde; waveform merkez; M logo |

### Durum: pending

---

## Task 4: Diğer 9 Sayfa — Türkçe + Aynı Görsel Dili + HUD Corner

**Kapsam:** `v4_chat.py / v4_agent.py / v4_memory.py / v4_skills.py / v4_computer.py / v4_browser.py / v4_tasks.py / v4_activity.py / v4_settings.py`
**Öncesi:** Task 3 (Home reference bitmeden diğer sayfaların palette mapping zor).
**Öncelik:** Yüksek
**Ana Kriter:** ←F19 – ←F27

### Uygulanacaklar

Her sayfa için TEK TEK değişiklikler (sıra önemli değil ama toplu 9 dosya):

1. **Başlıklar Türkçe:** V4PageHeader title ANA SAYFA yerine kendi adı; alt başlık.
2. **Wrapper → V4HUDPanel:** Tüm ana wrapper V4HUDPanel olsun (HUD corner).
3. **Durum String'leri Türkçe Map:** snap phase/approval/verification → Türkçe (Task 2'deki `_TR_PHASE` ve `_TR_STATUS` sabitleri).
4. **Boş Durumlar Türkçe:**
   - "Sohbet bekleniyor. Mesaj gönderdiğinizde burada görünecektir."
   - "AJAN HAZIR — Komut bekleniyor."
   - "HAFIZA VERİSİ YOK — Politika gereği maskeli."
   - "Öğrenilmiş YETENEKLER listeleniyor." (sahip ise) / "SON KULLANILAN — BİLDİRİLMİYOR."
   - "BİLGİSAYAR: Windows sistemi. Windows listesi BİLDİRİLMİYOR."
   - "TARAYICI durumu BİLİNMİYOR."
   - "GÖREV YOK — Sistem HAZIR."
   - "OLAY YOK — İlk görev başladığında burada gözükecek."
   - "AYARLAR: Genel / Sunucu / Model / Ses."
5. **V4Settings API key label:** "API ANAHTARI (HER ZAMAN MASKELİ)" Türkçe; save/load buton Türkçe.
6. **V4Activity filtreler Türkçe:** TÜMÜ / AKIL YÜRÜTME / EYLEM / GÖZLEM / DOĞRULAMA / ONAY / KURTARMA / BAŞARI / HATA.
7. **V4Tasks 5 grup Türkçe:** AKTİF / SON / TAMAMLANDI / İPTAL EDİLDİ / HATA.
8. **V4Skills 4 bölüm:** ÖĞRENİLMİŞ / SON KULLANILAN / ONARIM GEREKEN / KULLANILABİLİR.

**Veri dürüstlüğü:** Sayfa başına 100% — sahte chat/URL/memory/görev YOK.

### Test Gereksinimleri

| ID | Tip | Durum | Kanıt |
|----|-----|-------|------|
| TR 4.1 | rule | Pass | Her sayfada Türkçe başlık string mevcut (9 ayrı grep) |
| TR 4.2 | rule | Pass | Her sayfada V4HUDPanel kullanımı (≥1 kere) |
| TR 4.3 | rule | Pass | 9 sayfada sahte data grep (ör: "google.com / example.com / CPU %52") — 0 sonuç |
| TR 4.4 | rubric (0-2, ≥1.5) | Puan 2 | Her sayfa HOME kopyası değil; AJAN execution center, SOHBET conversation, vs — amaca uygun |

### Durum: pending

---

## Task 5: Test Paketi Güncelleme + V3 Regression Kontrol

**Kapsam:** `tests/test_app_v4_shell.py` + `tests/test_v4_pages_smoke.py` güncelle + YENİ test case'ler
**Öncesi:** Task 1–4 bitti (kod yazıldıktan sonra test güncelleme)
**Öncelik:** Yüksek
**Ana Kriter:** ←F29, ←F30

### Yeni Eklenecek Testler

1. `test_no_hermes_display_string_in_ui_modules` — v4_* ve app_v4_shell .py dosyalarında regex `(?i)hermes` ara; istisna: NAV_ITEMS ingilizce anahtar / import line / comment.
2. `test_nav_items_display_labels_turkish` — app_v4_shell'de buton text'leri oluşturulduğunda _NAV_TR kullanıldığını AST kontrolü veya string karşılaştırması.
3. `test_core_audio_probe_instantiation_fallback_safe` — AudioProbe instantiate → ImportError yoksa start/stop/destroy; ImportError varsa safe 0.0 döner.
4. `test_v4_hud_panel_4_corners` — V4HUDPanel widget count corners.
5. Mevcut 42 test: HİÇBİRİ fail etmemeli (FAIL YOK; SKIP sandbox başa çıkma).

### Test Gereksinimleri

| ID | Tip | Durum | Kanıt |
|----|-----|-------|------|
| TR 5.1 | rule | Pass | `pytest -q tests/test_v4_* tests/test_app_v4_shell.py` → 0 FAIL, ≥39 PASS |
| TR 5.2 | rule | Pass | `test_no_hermes_*` PASS |
| TR 5.3 | rubric (0-2, ≥1.5) | Puan 2 | Yeni 5 test okunabilir; tekrarlanan assert YOK; coverage yeterli |

### Durum: pending

---

## Task 6: Gerçek GUI Runtime Validation 3 Çözünürlük × 10 Sayfa

**Kapsam:** Runtime GUI script (geçici) + manual sonuç kontrol
**Öncesi:** Task 5 testler 0 FAIL
**Öncelik:** EN YÜKSEK (kullanıcı kabul kriteri ZORUNLU)
**Ana Kriter:** ←F28, ←F31, ←F32

### Uygulanacaklar

1. Geçici `_muvahhid_gui_probe.py` (Task 15 sonrası silinecek) yaz: 3 boyut 1200×800 / 1366×768 / 1920×1080; her boyutta 10 sayfa dolaş; bounds check (clip overlap).
2. Manuel olarak:
   - HOME'da Core canvas boyutu ölç;
   - Chat panel genişliği ölç;
   - Sağ alttaki "Uyandırma kelimesi: MUVAHHİD" doğru yazılı mı?
   - Mic butonuna tıkla (güvenli — audio stream açılırsa / kapanırsa);
   - Wake word display kontrolü.
3. Son 30 saniye: Tcl/Tk script error YOK → başarılı.

### Test Gereksinimleri

| ID | Tip | Durum | Kanıt |
|----|-----|-------|------|
| TR 6.1 | rule | Pass | 30x30 page_check issue_count 0 (GUI prob sonucu) |
| TR 6.2 | rule | Pass | TclError sayısı 0 log'da |
| TR 6.3 | rubric (0-2, ≥1.5) | Puan 2 | Görsel izlenim: Jarvis/AI OS hissi; Python dashboard hissi YOK |

### Durum: pending

---

## Task 7: Bağımsız Review + Final 9-Bölüm Rapor

**Kapsam:** review.md (Review safhasında yazılacak) + Final Report (kullanıcı cevabı)
**Öncesi:** Task 6 bittikten sonra (sıra)
**Öncelik:** Yüksek

### Review Checkpoints (review.md içeriği):

1. AC F1–F32 her biri için ← ilgili task kanıt var mı?
2. HUD corner motifleri — 10 sayfadan en az 10 panelde görülüyor mu?
3. "Hermes" görünüyor mu (NAV_ITEMS anahtarı hariç)?
4. Sahte data üretimi var mı (grep CPU/RAM %)?
5. Core ses reaktif: AudioProbe destroy güvenli mi?
6. V3 regression: 136+ test eski paket PASS mi?

### Final Rapor Bölümleri (Task 24 son kullanıcı cevabı):
1. Değişen / oluşturulan dosyalar
2. Yeni UI bileşenleri (V4HUDPanel / V4CoreCanvas / AudioProbe / V4HUDStatusChip)
3. Tüm 10 ekran durumu (sayfa × veri kaynağı × dürüst durum)
4. Test sonuçları: 42 + 5 yeni test → ≥ 45 PASS, 0 FAIL
5. Runtime GUI test: 30x30 0 sorun
6. Responsive doğrulama: 3 boyut × 10 sayfa clipping YOK
7. Ses entegrasyonu / Wake word entegrasyonu / Audio-reactive Core integration status (gerçek / safe fallback)
8. Kalan sorunlar / Known issues
9. Backend entegrasyon boşlukları (ileride V4 projection genişletilince otomatik doldurulacak alanlar)

### Durum: pending

---

## Kapsam Dışı (Cancelled) — Onaylı

- Backend V3 kanonik event, v4_projection, v4_store, orchestrator, memory runtime değişikliği: KAPSAM DIŞI (user mimari koru kuralı). İptal gerekçesi: Spec K5 + Task 24 Kısıtlar. Onay: user Task 15 Kural 1.
