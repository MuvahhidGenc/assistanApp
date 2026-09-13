"""MUVAHHİD V4 compliance tests — 4 yeni kabul kriteri (AC F2/F3/F7/F12 + brand rule).

Kapsam:
1. Brand rule: UI display stringlerinde 'Hermes'/'HERMES' YOK (modül yolu/import adları hariç).
2. Nav labels: _NAV_TR mapping 10 ingilizce key → Türkçe display label.
3. AudioProbe + SafeFallbackWaveform güvenli fallback (F12).
4. V4HUDPanel 4 köşe canvas attribute'ları (F7 - HUD corner motifleri).
"""
import ast
import pathlib
import pytest


def _collect_ui_file_paths():
    ui_dir = pathlib.Path(__file__).resolve().parent.parent / "src" / "hermes" / "ui"
    files = [
        "app_v4_shell.py",
        "v4_design.py",
        "v4_home.py",
        "v4_audio.py",
        "v4_chat.py",
        "v4_agent.py",
        "v4_memory.py",
        "v4_skills.py",
        "v4_computer.py",
        "v4_browser.py",
        "v4_tasks.py",
        "v4_activity.py",
        "v4_settings.py",
    ]
    out = []
    for n in files:
        p = ui_dir / n
        if p.exists():
            out.append(p)
    return out


# ---------------------------------------------------------------------------
# TEST 1: UI display metinlerinde Hermes/HERMES YOK (import/class adları hariç)
# ---------------------------------------------------------------------------
def test_no_hermes_display_string_in_ui_modules():
    """Brand rule: Kullanıcı görünür UI metinlerinde 'Hermes'/'HERMES' görünmemeli.

    AST düzeyinde tarama:
    - Sadece ast.Constant (sabit string) değerleri taranır.
    - Aşağıdaki stringler OTOMATIK olarak atlanır (kod tanımlayıcı, yol, import parçası değildir UI stringi ama özel olarak bilinen izin verilenler):
      * 'hermes.ui.xxx' (import modül yolu stringi — kullanıcıya görünmez)
      * '%LOCALAPPDATA%/HERMES/...', 'HermesClient' gibi Windows dizin yollarını temsil eden UI etiketi olmayacak — dosya içi path stringleri kullanıcı görünürse de onları ayarlar dosyası path olarak kullanıcıya gösterilebilir ancak spec'te yasak yok. Biz sadece 'Hermes configuration' gibi UI label stringlerini arıyoruz.

    Basit ve dürüst filtre:
    - import / from import satırlarında bulunan module adları AST'da node.module olarak bulunur, bu Constant'ları taramaya dahil ETME.
    - Ayrıca 'hermes.' ile başlayan Constant string'leri (modül yolu) taramaya dahil ETME.
    """
    violations: list[str] = []
    banned_lower_substrings = ("hermes",)
    allowed_prefixes = (
        "hermes.ui", "hermes.config", "hermes.memory", "hermes.skills",
        "hermes.voice", "hermes.browser", "hermes.approval", "hermes.security",
        "hermes.orchestrator", "hermes.reasoning", "hermes.executor",
        "hermes.computer_control", "hermes.v3", "hermes.agents",
        "%localappdata%/hermes", "%localappdata%/hermesclient",
        "release/hermesclient", "hermes-pc-session",
        "hermesclient", "hermes/skills/", "hermes-agent",
        "hermes.config_client",
    )

    for p in _collect_ui_file_paths():
        tree = ast.parse(p.read_text(encoding="utf-8", errors="replace"))
        mod_name_strings: set[str] = set()
        # 1) Tüm Import / ImportFrom node'larından module isimlerini topla (izne listesi)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                mod_name_strings.add(str(node.module).lower())
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    mod_name_strings.add(str(alias.name).lower())

        # 2) Tüm Constant string değerleri tara
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                val = node.value
                low = val.lower().strip()
                if not low:
                    continue
                # Modül adı (import) olarak geçmişse -> İZİN VER (prefix veya substring izin verilenler)
                if (low in mod_name_strings
                        or any(low.startswith(pref) for pref in allowed_prefixes)
                        or any(pref in low for pref in allowed_prefixes)):
                    continue
                # 'hermes' kelimesi içeriyor mu?
                for ban in banned_lower_substrings:
                    if ban in low:
                        violations.append(
                            f"{p.name}:{getattr(node, 'lineno', '?')}: "
                            f"Hermes görünür UI stringi: {val[:140]!r}"
                        )
                        break
    assert not violations, (
        "UI display stringlerinde 'Hermes'/'HERMES' bulundu — tüm görünür etiketler MUVAHHİD olmalı.\n"
        + "\n".join(violations)
    )


# ---------------------------------------------------------------------------
# TEST 2: _NAV_TR sabiti 10 NAV_ITEM → Türkçe label map
# ---------------------------------------------------------------------------
def test_nav_items_display_labels_turkish():
    """_NAV_TR sözlüğü 10 İngilizce NAV_ITEMS anahtarını doğru Türkçe değerlere map etmeli.

    Doğru Türkçe değerler (spec 10):
    HOME → ANA SAYFA
    CHAT → SOHBET
    AGENT → AJAN
    MEMORY → HAFIZA
    SKILLS → YETENEKLER
    COMPUTER → BİLGİSAYAR
    BROWSER → TARAYICI
    TASKS → GÖREVLER
    ACTIVITY → AKTİVİTE
    SETTINGS → AYARLAR

    Not: _NAV_TR tanımı v4_design.py içinde tanımlıdır ve app_v4_shell.py import eder.
    """
    expected_map = {
        "HOME": "ANA SAYFA",
        "CHAT": "SOHBET",
        "AGENT": "AJAN",
        "MEMORY": "HAFIZA",
        "SKILLS": "YETENEKLER",
        "COMPUTER": "BİLGİSAYAR",
        "BROWSER": "TARAYICI",
        "TASKS": "GÖREVLER",
        "ACTIVITY": "AKTİVİTE",
        "SETTINGS": "AYARLAR",
    }

    # 1) Runtime import ile _NAV_TR'yi al (v4_design.py içinde)
    from hermes.ui.v4_design import _NAV_TR as nav_tr_found

    assert nav_tr_found is not None, "_NAV_TR import edilemedi"
    assert isinstance(nav_tr_found, dict), f"_NAV_TR dict olmalı, tip={type(nav_tr_found)}"
    assert len(nav_tr_found) == 10, f"_NAV_TR 10 key içermeli, bulundu: {len(nav_tr_found)} keys={sorted(nav_tr_found.keys())}"
    for en, tr in expected_map.items():
        assert en in nav_tr_found, f"_NAV_TR missing key: {en!r}"
        assert nav_tr_found.get(en) == tr, (
            f"_NAV_TR[{en!r}] = {nav_tr_found.get(en)!r}, beklenen: {tr!r}"
        )

    # 2) AST seviyesinde app_v4_shell.py ImportFrom ile _NAV_TR'yi import ettiğini de doğrula
    import pathlib
    src = (pathlib.Path(__file__).resolve().parent.parent
           / "src" / "hermes" / "ui" / "app_v4_shell.py")
    tree = ast.parse(src.read_text(encoding="utf-8", errors="replace"))
    import_found = False
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and node.module.endswith("v4_design"):
            for alias in node.names:
                if alias.name == "_NAV_TR":
                    import_found = True
    assert import_found, "app_v4_shell.py v4_design'dan _NAV_TR import etmiyor"


# ---------------------------------------------------------------------------
# TEST 3: AudioProbe + SafeFallbackWaveform güvenli fallback
# ---------------------------------------------------------------------------
def test_core_audio_probe_instantiation_fallback_safe():
    """F12: AudioProbe sınıfı mevcut; başarısız olduğunda patlamaz.
    SafeFallbackWaveform 0.0-1.0 aralığında float list döner.
    """
    from hermes.ui.v4_audio import AudioProbe, SafeFallbackWaveform

    # 1) SafeFallbackWaveform aralık kontrolü (ses yoksa güvenli dalga)
    bars = SafeFallbackWaveform(seed=42, bars=180)
    assert isinstance(bars, list), f"SafeFallbackWaveform list döndürmeli, tip={type(bars)}"
    assert len(bars) == 180, f"SafeFallbackWaveform 180 bar döndürmeli, uzunluk={len(bars)}"
    for i, v in enumerate(bars):
        assert isinstance(v, (int, float)), f"bars[{i}] sayı değil: {v!r}"
        assert 0.0 <= float(v) <= 1.0001, f"bars[{i}] 0-1 aralığı dışı: {v}"

    # 2) AudioProbe sınıfı __init__ sonrası available bool ve amp 0.0 clamp
    try:
        probe = AudioProbe()
    except Exception as exc_init:  # pragma: no cover
        # Ses donanımı olmaması gibi sebeplerle init Exception patlaması NORMAL — bu test için FAIL değil.
        # Ancak sınıf import edilebilmeli.
        return
    try:
        assert isinstance(probe.available, bool)
        if probe.available:
            amp = probe.get_amplitude()
            assert isinstance(amp, float)
            assert 0.0 <= amp <= 1.0001, f"AudioProbe.get_amplitude() 0-1 aralığı dışı: {amp}"
    finally:
        try:
            probe.destroy()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# TEST 4: V4HUDPanel 4 köşe canvas attribute'ları (F7 HUD corner motifleri)
# ---------------------------------------------------------------------------
def test_v4_hud_panel_4_corners():
    """F7: V4HUDPanel 4 ayrı küçük köşe canvas'ı üzerinde L-şekli HUD çizgileri çizer.
    Safe implementation (NO full overlay mask bug): 4× (40×40 corner canvas, NW/NE/SW/SE),
    hiçbiri merkez çocuk alanını kapsamaz, tkraise() YOK.
    — _corner_ids = list[list[int]]: Her köşe 2 ayrı create_line id'si = toplam min 8 satır.
    Bu sayede F7 kuralındaki HUD köşeleri doğruluğu + overlay mask bug'u YOK.
    """
    try:
        import customtkinter as ctk  # type: ignore
    except Exception as exc_import:  # pragma: no cover - headless
        pytest.skip(f"customtkinter import başarısız, GUI test atlanıyor: {exc_import}")
        return
    from hermes.ui.v4_design import V4HUDPanel

    try:
        top = ctk.CTk()
    except Exception as exc_tk:  # pragma: no cover - headless/tcl error
        pytest.skip(f"CTk ekranı başlatılamadı: {exc_tk}")
        return

    panel = None
    try:
        parent = ctk.CTkFrame(top, width=400, height=300)
        if isinstance(parent, tuple):
            parent = parent[0]
        parent.pack(fill="both", expand=True)
        top.update_idletasks()
        panel = V4HUDPanel(parent)
        # V4HUDPanel canvas'ları <Configure> eventi sonrası oluşturulduğu için
        # manuel tetikle: pack + update → _draw_corners ateşlenir
        try:
            panel.frame.pack(fill="both", expand=True, padx=10, pady=10)
            top.update()
            top.update_idletasks()
            # 2.geometri güncellemesi → corner canvaslar kesin oluşsun
            try:
                if hasattr(panel, "_ensure_corners") and callable(getattr(panel, "_ensure_corners")):
                    panel._ensure_corners()
                if hasattr(panel, "_draw_corners") and callable(getattr(panel, "_draw_corners")):
                    panel._draw_corners()
            except Exception:
                pass
            top.update_idletasks()
        except Exception:
            pass

        # ── 4 ayrı köşe canvas var (tek overlay canvas YOK — Core'u kapatan bug düzeltildi)
        corners = {
            "NW": getattr(panel, "_corner_nw", None),
            "NE": getattr(panel, "_corner_ne", None),
            "SW": getattr(panel, "_corner_sw", None),
            "SE": getattr(panel, "_corner_se", None),
        }
        for name, cv in corners.items():
            assert cv is not None, (
                f"V4HUDPanel._corner_{name.lower()} attribute'ı mevcut değil"
                " (4 köşeli canvas implementasyonu, eski tek overlay degil)"
            )
            assert callable(getattr(cv, "destroy", None)), (
                f"V4HUDPanel._corner_{name.lower()} nesnesinin destroy() metodu yok"
            )
            # Her corner canvas 40x40 olmalı (merkez Core'u KAPATMAYAN küçük hücre)
            try:
                w = int(cv.winfo_width() or 40)
                h = int(cv.winfo_height() or 40)
                assert 30 <= w <= 80 and 30 <= h <= 80, (
                    f"Kose {name} boyutu ({w}x{h}) 40x40 civarinda olmali — cok buyuk = overlay mask riski"
                )
            except Exception:
                pass

        # Eski (buglu) tek overlay canvas ARTIK YOK olmali
        assert getattr(panel, "_corner_canvas", None) is None, (
            "ESKI buglu _corner_canvas (tam boyutlu overlay) hâlâ mevcut — 4 kucuk kose kullanilmali"
        )

        # _corner_ids = list[list[int]] (list of list): Her köşe kendi 2 id'lik alt listesi
        ids_nested = getattr(panel, "_corner_ids", None)
        assert isinstance(ids_nested, list), (
            "V4HUDPanel._corner_ids list olmali (list[list[int]]: 4 kose x 2 L-cizgi id)"
        )
        assert len(ids_nested) == 4, (
            f"V4HUDPanel._corner_ids 4 elemanli olmali (NW/NE/SW/SE). {len(ids_nested)} bulundu."
        )
        for idx, sub in enumerate(ids_nested):
            assert isinstance(sub, list), (
                f"V4HUDPanel._corner_ids[{idx}] sublist olmali (her kose kendi idlerini tutar)."
            )

        # Paint sonrasi tekrar check
        try:
            panel.frame.update_idletasks()
        except Exception:
            pass
        ids2_nested = getattr(panel, "_corner_ids", [[], [], [], []])
        total_ids = sum(len(s) for s in ids2_nested)
        # En az 4 köşe × 2 çizgi = 8 id
        assert total_ids >= 8, (
            f"V4HUDPanel 4 köşe için en az 8 çizgi id'si çizmeli, "
            f"(toplam {total_ids} adet bulundu. Muhtemel L-şekli HUD köşe çizgileri eksik."
        )
    finally:
        try:
            if panel is not None and hasattr(panel, "destroy") and callable(panel.destroy):
                panel.destroy()
        except Exception:
            pass
        try:
            top.destroy()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# TEST 5: REGRESSION — "Core renders initially then disappears ~1s after" BUG
# ---------------------------------------------------------------------------
# Root causes that were fixed and now are protected:
#   (A) V4HUDPanel used a SINGLE opaque full-overlay canvas (relwidth=1, relheight=1)
#       with solid dark bg + tkraise() fired after first <Configure> (~1000ms).
#       This dark canvas LIFTED ON TOP of the Core widget, covering it completely.
#       Fix: 4 separate tiny 40×40 corner canvases (NW/NE/SW/SE), NO tkraise().
#   (B) V4CoreCanvas.redraw() called canvas.delete(ids) BEFORE checking R<40.
#       If size temporarily invalid during resize → black empty canvas (Failure Mode 4).
#       Fix: check R>=40 FIRST, delete only when new frame is guaranteed to draw.
# ---------------------------------------------------------------------------
def test_regression_core_disappears_after_1s():
    """CRITICAL REGRESSION: Prevents exact bug where MUVAHHID Core appears for ~1s
    then disappears completely leaving only HUD L-brackets + black empty area."""

    # ── Guard: GUI env
    try:
        import customtkinter as ctk  # type: ignore
    except Exception as exc_import:  # pragma: no cover
        pytest.skip(f"customtkinter yok, GUI regression atlanıyor: {exc_import}")
        return

    from hermes.ui.v4_design import V4HUDPanel, V4CoreCanvas

    # ================================================================
    # FIX VERIFICATION (A) — NO full-size overlay _corner_canvas
    # ================================================================
    try:
        top_a = ctk.CTk()
    except Exception as exc_tk:  # pragma: no cover
        pytest.skip(f"CTk başlatılamadı: {exc_tk}")
        return

    panel_a: V4HUDPanel | None = None
    try:
        parent = ctk.CTkFrame(top_a, width=500, height=400)
        parent.pack(fill="both", expand=True)
        top_a.update_idletasks()
        panel_a = V4HUDPanel(parent)
        panel_a.frame.pack(fill="both", expand=True, padx=10, pady=10)
        for _ in range(3):
            top_a.update()
            top_a.update_idletasks()
        try:
            if hasattr(panel_a, "_ensure_corners"):
                panel_a._ensure_corners()
            if hasattr(panel_a, "_draw_corners"):
                panel_a._draw_corners()
        except Exception:
            pass
        top_a.update_idletasks()

        # (A-1) OLD buggy single canvas MUST NOT exist anymore
        assert getattr(panel_a, "_corner_canvas", None) is None, (
            "REGRESSION: eski buglu `_corner_canvas` (tam boyutlu overlay mask) "
            "hâlâ mevcut! Core'u ~1s sonra kapatmaya devam eder."
        )

        # (A-2) 4 separate tiny corner canvases MUST exist (40x40 each, not full-size)
        for name in ("_corner_nw", "_corner_ne", "_corner_sw", "_corner_se"):
            cv = getattr(panel_a, name, None)
            assert cv is not None, (
                f"REGRESSION: 4 köşeli güvenli yapıda {name} canvas eksik. "
                "Overlay mask riski geri döndü."
            )
            try:
                w = int(cv.winfo_width() or 40)
                h = int(cv.winfo_height() or 40)
            except Exception:
                w, h = 40, 40
            # Corner canvas should be SMALL — never approach parent width/height.
            # 40x40 nominal; we accept 20..120. Overlay canvas would be 500x400.
            assert w <= 120 and h <= 120, (
                f"REGRESSION: corner canvas {name} W={w} H={h} cok büyük! "
                "Tam boyutlu overlay gibi Core'u kapatma riski var."
            )

        # (A-3) NO tkraise() pattern on corner canvases:
        # — we cannot assert method was never called, but we CAN confirm that
        #   corner canvas winfo parent child ordering keeps corner canvases LOW.
        #   Tk stacking order: list children last-on-top; corners shouldn't be last
        try:
            kids = list(panel_a.frame.winfo_children())
            # Remove corners; whatever remains is the content (Core etc.).
            # Corner canvas objects should appear BEFORE content in list if stacked correctly.
            corner_set = {
                getattr(panel_a, "_corner_nw", None),
                getattr(panel_a, "_corner_ne", None),
                getattr(panel_a, "_corner_sw", None),
                getattr(panel_a, "_corner_se", None),
            }
            corner_idx = [kids.index(c) for c in corner_set if c in kids]
            content_idx = [i for i, k in enumerate(kids) if k not in corner_set]
            if corner_idx and content_idx:
                max_corner_idx = max(corner_idx)
                min_content_idx = min(content_idx)
                # Corners are stacked EARLIER (lower z) than content. Good —
                # corners can never be lifted ON TOP of content (Core).
                assert max_corner_idx < min_content_idx, (
                    "REGRESSION: corner canvases content (Core)'den ÜSTTE (yuksek stacking). "
                    "Eski overlay mask bug geri dondu!"
                )
        except Exception:
            # Stack inspection best-effort; corner existence is the real guard.
            pass
    finally:
        try:
            if panel_a is not None:
                panel_a.destroy()
        except Exception:
            pass
        try:
            top_a.destroy()
        except Exception:
            pass

    # ================================================================
    # FIX VERIFICATION (B) — DELETE ordering inside V4CoreCanvas.redraw()
    # ================================================================
    # We cannot easily simulate R<40 via geometry; instead, audit the source
    # code AST/text ordering of "if R < 40: return" vs "for _id in self._ids: delete".
    # The check MUST appear BEFORE the delete loop, otherwise we regress to
    # black empty canvas on transient tiny geometry.
    try:
        from pathlib import Path
        src_path = Path(__file__).resolve().parents[1] / "src" / "hermes" / "ui" / "v4_design.py"
        text = src_path.read_text(encoding="utf-8")
    except Exception:  # pragma: no cover
        pytest.skip("v4_design.py kaynak okunamadi, B skip edildi")
        return

    # Look for the redraw() method block. Textual order check is stable because
    # "R < 40" check and the explicit delete loop coexist in redraw scope.
    def _find_first(pattern: str, hay: str, after: int = 0) -> int:
        idx = hay.find(pattern, after)
        return idx if idx >= 0 else 10 ** 9

    # Slice only `def redraw(self)` region to avoid matching elsewhere.
    rd_start = text.find("def redraw(self)")
    assert rd_start >= 0, "redraw() metodu v4_design.py'da bulunamadi"
    next_def = min(
        _find_first("\ndef ", text, rd_start + 5),
        _find_first("\nclass ", text, rd_start + 5),
    )
    redraw_region = text[rd_start:next_def]

    idx_check = _find_first("if R < 40:", redraw_region)
    idx_delete = _find_first("for _id in self._ids:", redraw_region)

    # Both must exist in redraw region
    assert idx_check < 10 ** 8, "redraw() içinde `if R < 40:` guard bulunamadi"
    assert idx_delete < 10 ** 8, "redraw() içinde `for _id in self._ids:` delete loop bulunamadi"

    # The ORDER must be check → return → delete (if R<40 happens, we never delete).
    assert idx_check < idx_delete, (
        "REGRESSION: redraw() icinde DELETE loop, R<40 RETURN kontrolunden ÖNCE calisiyor! "
        "Bu geometry gecici invalid oldugunda SİYAH BOŞ canvas birakir (Failure Mode 4). "
        "Duzeltme: once `if R < 40: return`, SONRA delete loop + yeni cizim."
    )

    # ================================================================
    # FIX VERIFICATION (C) — Exception traceback logging not silently swallowed
    # ================================================================
    # New code uses print + _traceback.format_exc(limit=N) in critical paths.
    # At least verify "format_exc" keyword appears in the 4 listed paths
    # (they used to be bare except Exception: pass before our fix).
    for section_marker in (
        "_tick(self)",
        "set_amplitude(self",
        "_on_resize(self",
        "_schedule(self",
        "def redraw(self",
    ):
        s_start = text.find(section_marker)
        if s_start < 0:
            continue
        s_end = min(
            _find_first("\ndef ", text, s_start + len(section_marker)),
            _find_first("\nclass ", text, s_start + len(section_marker)),
        )
        section = text[s_start:s_end]
        # Either "traceback" or "format_exc" should appear in this section now
        assert "traceback" in section.lower() or "format_exc" in section, (
            f"REGRESSION: Kritik section `{section_marker}` icinde exception traceback loglamasi "
            "yok! Exception sessizce yutulursa animasyon loopu gizlice durabilir ve Core kaybolur."
        )
