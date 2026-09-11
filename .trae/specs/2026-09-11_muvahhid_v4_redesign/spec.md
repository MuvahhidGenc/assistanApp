# MUVAHHİD V4 REDESIGN — SPESİFİKASYON

## Problemin Tanımı

Mevcut Hermes V4 UI, kullanıcının 24.09.2026 tarihli ana isteği uyarınca **tamamen yeniden tasarlanacak**:
- Marka: Hermes → **MUVAHHİD** (UI metinlerinde "HERMES" hiç görünmeyecek)
- Ana sayfa kompozisyonu: LEFT nav + CENTER büyük yaşayan MUVAHHİD AI Core + RIGHT kalıcı sohbet + BOTTOM minimal durum
- Görsel dil: Kırmızı/siyah premium yerini **mavi-cyan birincil + turuncu ikincil futuristik HUD/Jarvis benzeri** komuta merkezi
- Ses etkileşimi: **gerçek ses mikrofon amplitüdü ile ses-reaktif AI Core** (düşük → yüksek dalga genliği)
- UI dili: TAMAMEN TÜRKÇE (NAV_ITEMS display, durum başlıkları, boş durumlar) — i18n framework GEREKSİN (sonraki aşama)
- Referans görsel: Kullanıcı tarafından eklenen ekran görüntüsü (MUVAHHİD — AI BİLGİSAYAR AJANI; CENTER M core, right SOHBET, left NAV, bottom durum)

## Hedef Kullanıcılar

1. **Birincil Kullanıcı (Masaüstü AI Operatörü):** MUVAHHİD ile metin/ses aracılığıyla Windows bilgisayar otomasyonu yapan profesyonel kullanıcı. HOME'dan çıkmadan komut verebilmeli.
2. **Geliştirici (Observability):** AKTİVİTE/AYARLAR ekranları ile debug/configuration yapan.

## Hedefler

1. **Görsel Uyum:** Referans görsel ile %80+ benzerlik (kompozisyon, renkler, motifler, hiyerarşi).
2. **Yaşayan Core:** Merkezdeki MUVAHHİD Core; 5+ eşmerkezli halka, parçacıklar, dalga formu, tarama çizgileri, M logosu, durum renk farklılıkları.
3. **Ses-Reaktif Mimari:** Mikrofon gerçek amplitüd → Core dalga genişliği, halka genleşmesi, parçacık aktivitesi. Gerçek değer yok → integration hook + safe fallback, SAHTE tekrarlanan animasyon YOK.
4. **Türkçe UI:** 10 ekran × tüm statik metin Türkçe. Durumlar: HAZIR / DİNLİYOR / DÜŞÜNÜYOR / ÇALIŞIYOR / KONUŞUYOR / ONAY BEKLİYOR / TAMAMLANDI / HATA.
5. **Mimari Koruma:** V3 runtime/orchestrator/security/approval/memory/skills DEĞİŞTİRİLMEYECEK. Sadece V4 UI presentation layer.
6. **Veri Dürüstlüğü:** Sahte metrik/connection/session YOK. 7 kanonik snap anahtarı dışı alan = BİLDİRİLMİYOR / KULLANILAMIYOR.

## Hedef Dışı (Non-Goals)

1. Multilingual i18n framework (sonraki aşama).
2. Ses tanıma/STT/TTS backend rewrite (mevcut VoiceAssistant kullan).
3. V3 kanonik event sistemi / v4_store / v4_projection değişikliği (sadece 7 snap anahtar + 3 public read-only loader).
4. Gösteriş için fake CPU/RAM/Network grafik, fake tab, fake görev üretimi.
5. Hermes → MUVAHHİD rebrand'ı kaynak kod klasör/modül isimlerinde (klasör `hermes/` KALIR; SADECE UI display string'leri değişir).

## Kısıtlar

- **K1:** CustomTkinter + Tk Canvas (webview yok).
- **K2:** Renkler: 6-digit hex, rgba() YASAK.
- **K3:** Git commit/push/reset/clean/revert YASAK.
- **K4:** Yazı tipleri: Segoe UI öncelikli (tüm UI); Consolas sadece uppercase teknik label/event_id/timestamp (kısıtlı).
- **K5:** 7 kanonik snap anahtarı: `phase, approval_state, verification_state, recovery_state, error_state, event_count, latest_event_id` — UI bunlar + 3 read-only loader (settings_store form, skills.loader.load_all_skills, user_config_path) dışında V3 runtime import EDEMEZ (test ile doğrulanır).
- **K6:** after() callback — destroy() içinde after_cancel() ZORUNLU, runaway timer YOK.
- **K7:** Hardcoded x/y absolute placement YOK; grid weights + minsize + scrollable frame ile responsive.
- **K8:** Wake word backend listesi (abi/akhi/dostum) DEĞİŞTİRİLMEZ; UI'da display label olarak "MUVAHHİD" gösterilir (durum: Uyandırma kelimesi: MUVAHHİD).

## Bağımlılıklar ve Varsayımlar

- Python 3.13; CustomTkinter 5.2.2; SpeechRecognition (ses amplitüd RMS probu için pyaudio backend — UI seviyesinde read-only).
- `voice_listener.py` + `voice/assistant.py` + `voice/stt.py` mevcut; VoiceAssistant'ın activity ("listening"/"speaking") ve microphone_available okunacak (read-only).
- `v4_store.V4UIStore` mevcut; 7 snap + `_events` canonical event list immutable. Store.apply() YASAK UI tarafında.

## Açık Sorular

1. **Audio amplitüd bridge kanalı:** V4 bridge şu anda yok. Varsayım: UI tarafında `AudioProbe` adında read-only pyaudio stream açan bir UI probe sınıfı; RMS değeri gerçek mikrofondan okunur, store'a yazılmaz (kullanıcı onayı istenmedi, spec yazılımında varsayım olarak takıldı).

---

## KABUL KRİTERLERİ (AC) — Kural (rule) + Rubrik (rubric)

### Genel Kabuller

- **F1 (rule):** `NAV_ITEMS` sabiti `app_v4_shell.py` L13 İLİŞKİSİZ OLARAK KALIR (sıra HOME→CHAT→…→SETTINGS); UI display label'ları Türkçe (ANA SAYFA / SOHBET / AJAN / HAFIZA / YETENEKLER / BİLGİSAYAR / TARAYICI / GÖREVLER / AKTİVİTE / AYARLAR). Kanıt: `grep NAV_ITEMS src/hermes/ui/app_v4_shell.py` çıktısı ilk 10 İngilizce anahtar; display buton textleri Türkçe.
- **F2 (rule):** Tüm UI ekranlarında dize olarak "Hermes" veya "HERMES" YOK (case-insensitive). Kanıt: `grep -ri "hermes" src/hermes/ui/v4_*.py src/hermes/ui/app_v4_shell.py --include="*.py" | grep -v "#" | grep -v "NAV_ITEMS\|PAGE_REGISTRY\|import"`.
- **F3 (rule):** modern_theme renkleri 6-digit hex; rgba() string bulunmaz. Kanıt: `test_no_rgba_usage_in_new_v4_modules` test PASS.
- **F4 (rule):** V4 sayfalarından hiçbiri orchestrator, reasoning, executor, security, approval, memory runtime, skill executor V3 runtime import etmez (settings_store, skills.loader, user_config_path İSTİSNA). Kanıt: `test_page_modules_do_not_import_v3_runtime_modules` PASS.
- **F5 (rule):** 7 kanonik snap anahtarı dışında store yeni alan eklemez. Kanıt: `GuardedStore` instantiation testi PASS (her sayfa için).
- **F6 (rubric, ölçek 0-2, eşik ≥1.5, kanıt = GUI screenshot x3 çözünürlük):** HOME kompozisyon benzerliği referansa: 0 = hiç benzemiyor; 1 = orta benzer; 2 = sol dar NAV + merkez büyük M Core + sağ kalıcı sohbet + alt 5 durum çubuğu %80 yerleşim/renk uyumlu.

### Tasarım Dili (Tüm Ekranlar)

- **F7 (rule):** HUD köşe detayları (V4HUDPanel sınıfı ile 4 köşede L-şekli küçük çizgi) tüm panellerde kullanılır (minimum HOME 3 panelde: sağ sohbet, sol NAV, alt durum + diğer 9 ekran en az 1 panel). Kanıt: `grep -c "V4HUDPanel" src/hermes/ui/v4_*.py` ≥ 10 adet.
- **F8 (rule):** Birincil accent = NEON_CYAN (#00e5ff); İkincil accent = ORANGE (#ff6b35) (HOME'da Core'da turuncu halka segmentleri referans görseldeki gibi). Başarı = HOLO_GREEN (#05ffa1); Uyarı/Onay = NEON_GOLD (#ffd60a); Başarısız = NEON_MAGENTA (#ff2a6d). Kanıt: modern_theme.py import listesinde bunlar var; sayfalarda renk kodu grep 6-digit ile.

### MUVAHHİD AI Core (HOME CENTER)

- **F9 (rule):** Core canvas boyutu min 480px (1200×800'de) / max 720px (1920×1080'de); responsive pencere genişliği ile lineer ölçek. Kanıt: GUI'de 1200'de ≥480, 1920'de ≤720 piksel ölçümü.
- **F10 (rule):** Core bileşenleri (görselde var olduğu gibi): (a) merkez 'M' glow logo (Consolas/Segoe UI bold cyan glow stroke); (b) çekirdek glow nucleus (dairesel gradient simülasyonu, çoklu çap artırılarak); (c) 4+ eşmerkezli orbital halka (iki tanesi kısmi TURUNCU segment, ikisi tam cyan, biri taramalı dashed); (d) 8+ parçacık (orbit noktaları); (e) merkezden yatay ses dalgası formu (150+ çubuklu bar waveform amplitude); (f) dış HUD çember scan mark; (g) breath yavaş animasyon + orbital hareket. Kanıt: Canvas widget count ≥ 200 object; 8 Core state renk farklılaşması.
- **F11 (rule):** Core 8 durum için renk/frekans farkı: HAZIR (sakin cyan); DİNLİYOR (parlak dalga, titreşim); DÜŞÜNÜYOR (altın sarı pulse); ÇALIŞIYOR (turuncu segment hızlı dönüş); KONUŞUYOR (yeşil pulse); ONAY BEKLİYOR (gold flash); TAMAMLANDI (yeşil glow); HATA (magenta pulse). Kanıt: Her durum için farklı palette mapping fonksiyonu var.
- **F12 (rule):** Audio-reactive mimari integration noktası mevcut. UI tarafında gerçeği okuyan bir AudioProbe (pyaudio stereo RMS okuma, read-only, 30 FPS callback) destroy'da stream kapatılır + timer iptal edilir. Gerçek amplitüd 0.0–1.0 → (1) waveform genliği çarpanı; (2) en dış 2 halka R genişliği 0–20% değişim; (3) parçacık alpha yoğunluğu. Eğer pyaudio yoksa veya mikrofon izni verilmediyse → SAFE FALLBACK: idle dalga (sabit küçük genlik, 0.3 Hz) ama DÜŞMEYEN (başarısız/kilitlenme olmadan). Kanıt: AudioProbe sınıfı __del__/destroy stream close + try/except ImportError graceful fallback mevcut.

### Navigasyon (LEFT)

- **F13 (rule):** Sidebar genişliği 150px (small) / 180px (large); 10 NAV item ikon + Türkçe label; seçili olan SUBTLE GLOW (border glow 1 px NEON_CYAN + ikon rengi, yüksek kontrastlı büyük buton YOK). Kanıt: GUI'de 1200×800 sidebar w = 150 ± 2.

### Header (TOP)

- **F14 (rule):** Header satırı: SOL "MUVAHHİD V4 • AI BİLGİSAYAR AJANI" (logo M icon + text); ORTA "FAZ | {durum}" (7 snap phase map: idle→HAZIR, execute→ÇALIŞIYOR, ...); ORTA2 "BAĞLANTI | {durum}"; ORTA3 "OLAY | {count}"; SAAT (HH:MM 24-saat). Kanıt: header widget'larında Türkçe faz/bağlantı/olay etiketleri var.
- **F15 (rule):** BAĞLANTI durumu event_count'dan TÜRETİLMEZ; ya gerçek connection heartbeat (YOKSA → BİLİNMİYOR MUTED gri). Kanıt: grep "event_count.*ÇEVRİM" 0 sonuç.

### Right Chat Panel (HOME)

- **F16 (rule):** HOME'da kalıcı sohbet paneli 300–360px genişlik (responsive: 1200→300px, 1920→360px); bileşenleri: (1) başlık "SOHBET" + ses aç/kapat + ayar icon; (2) scrollable timeline (User bubble sağ, MUVAHHİD bubble sol M logo ile); (3) 4 öneri chip: "Bilgisayarımı kontrol et / Bir web sayfası aç / Yeni bir görev başlat / Hafızamda ara"; (4) composer: attachment icon + "Mesajınızı yazın..." + mic icon + send icon; (5) en altta durum çubuğu: mic icon "Sesli komut hazır | Uyandırma kelimesi: MUVAHHİD". Kanıt: HOME paneli count; widget isimleri SOHBET / Uyandırma kelimesi / Mesajınızı yazın içeriyor.
- **F17 (rule):** Sahte chat mesajı YOK. No data durumunda karşılama mesajı 1 tane: "Merhaba! Ben Muvahhid. Size nasıl yardımcı olabilirim?" (statik karşılama, 1 kez, fabrikasyon çoklu konuşma YOK). Sonraki mesajlar sadece olaylar geldikçe timeline'da görünür.

### Bottom Status Bar (HOME)

- **F18 (rule):** Alt çubukta 5 tile: (1) AJAN / HAZIR; (2) ONAY / GEREKMİYOR; (3) DOĞRULAMA / BEKLEMEDE; (4) KURTARMA / YOK; (5) HATA / YOK. Değerler 7 snap anahtarından dürüst map; boşluk simge nokta ile (icon + label + nokta + değer). Kanıt: 5 tile var; her birinde icon+label+değer.

### Diğer 9 Ekran (F19-F27)

Her ekran: (a) MUVAHHİD görsel dili + HUD corner; (b) Türkçe başlık/metin; (c) boş durumlar dürüst (BİLDİRİLMİYOR / VERİ YOK); (d) her bir sayfa HOME kompozisyonunun kopyası DEĞİL (sayfa amaca uygun).

- **F19 (rule / SOHBET):** Tam ekran konuşma alanı (HOME sohbetin genişletilmişi).
- **F20 (rule / AJAN):** Otonom görev yürütme merkezi (execution center, büyük durum + onay banner + context).
- **F21 (rule / HAFIZA):** 4 grup (OTURUM / ANI / UZUN SÜRELİ / BAĞLAM) + policy gated NO DATA.
- **F22 (rule / YETENEKLER):** Öğrenilmiş/Recent/Onarım/Kullanılabilir 4 bölüm.
- **F23 (rule / BİLGİSAYAR):** Windows sistem merkezi (GERÇEK platform/socket/uptime).
- **F24 (rule / TARAYICI):** Tarayıcı durum merkezi (NOT REPORTED).
- **F25 (rule / GÖREVLER):** 5 grup (AKTİF / SON / TAMAM / İPTAL / HATA).
- **F26 (rule / AKTİVİTE):** 9 filtre çipi + son 200 olay timeline.
- **F27 (rule / AYARLAR):** 8 grup (GENEL / SUNUCU / MODEL / SES / UYANDIRMA / BİLDİRİMLER / UI / GELİŞMİŞ) → API key maskeli.

### Responsive

- **F28 (rule):** 3 boyut (1200×800 / 1366×768 / 1920×1080) × 10 ekran → clipping 0; overlap 0; horizontal scroll YOK; Core hiçbir zaman kesilmez; sohbet paneli hiçbir zaman negatif genişliğe düşmez. Kanıt: Otomatik GUI 30x30 page_check issue_count 0.

### Testler

- **F29 (rule):** 42 testten önceki V4 paketi (Home/Shell/Pages Smoke) içinde hiçbir test düşmez (FAIL YOK, SKIP = sandbox TclError kabul edilir).
- **F30 (rule):** Yeni eklenen MUVAHHİD marka tests: (1) test_no_hermes_string_in_ui_modules PASS; (2) test_nav_items_display_labels_turkish PASS; (3) test_core_audio_probe_instantiation PASS (sandbox'ta skip olsun).

### Gerçek GUI

- **F31 (rule):** 3 çözünürlük x 10 sayfa TclError = 0; runaway callback = 0; after_cancel = destroy sonrası timer çalışmaz.
- **F32 (rubric 0-2, eşik ≥1.5):** Genel izlenim: 0 = Python dashboard; 1 = karmaşık ama futuristik hissi zayıf; 2 = gerçek Jarvis / AI işletim sistemi hissi veriyor (kullanıcı onayı sonrası).
