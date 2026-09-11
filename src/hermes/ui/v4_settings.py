from __future__ import annotations

from pathlib import Path
from typing import Any

from hermes.ui.modern_theme import (
    BUTTON,
    BUTTON_HOVER,
    CARD,
    CARD_BORDER,
    FONT_HUD,
    HOLO_GREEN,
    MUTED,
    NEON_BLUE,
    NEON_CYAN,
    NEON_GOLD,
    NEON_MAGENTA,
    TEXT,
    TEXT_BRIGHT,
)
from hermes.ui.v4_design import (
    V4Card,
    V4EmptyState,
    V4HUDPanel,
    V4PageHeader,
    V4Panel,
    V4SectionHeader,
    V4StatusBadge,
    V4StatusTile,
)

try:
    import customtkinter as ctk  # type: ignore
except Exception:  # pragma: no cover
    ctk = None  # type: ignore

try:
    from hermes.config.settings_store import (  # type: ignore
        SettingsFormData,
        load_settings_form,
        save_settings_form,
    )
except Exception:  # pragma: no cover
    SettingsFormData = None  # type: ignore
    load_settings_form = None  # type: ignore
    save_settings_form = None  # type: ignore

try:
    from hermes.config_client import user_config_path  # type: ignore
except Exception:  # pragma: no cover
    user_config_path = None  # type: ignore


def _mask(s: str | None, keep: int = 2, mask: str = "●") -> str:
    if s is None:
        return ""
    v = str(s)
    if len(v) <= keep * 2:
        return mask * max(4, len(v))
    return v[:keep] + mask * max(8, len(v) - keep * 2) + v[-keep:]


_SECRET_HINTS = ("api_key", "apikey", "password", "secret", "token", "credential", "cookie", "authorization")


def _scrub_line(line: str) -> str:
    low = line.lower()
    if any(h in low for h in _SECRET_HINTS) and ":" in line:
        head, tail = line.split(":", 1)
        stripped_tail = tail.strip()
        if stripped_tail and stripped_tail not in {"", null_tok_null}:
            cleaned = stripped_tail.strip(chr(34) + chr(39))
            return f"{head.rstrip()}: {_mask(cleaned, keep=1)}"
    return line


# Sentinel for null check above (avoid undefined)
null_tok_null = "null"


class V4Settings:
    def __init__(self, parent: Any, store: Any) -> None:
        self.store = store
        self._sub: Any = None
        self._timers: list[str] = []

        if ctk is None:  # pragma: no cover
            raise RuntimeError("customtkinter unavailable")

        self.root = ctk.CTkFrame(parent, fg_color="transparent")
        self.widget = self.root
        self.root.pack(fill="both", expand=True, padx=18, pady=14)
        self.root.rowconfigure(2, weight=1)
        self.root.columnconfigure(0, weight=1)

        self.header = V4PageHeader(
            self.root,
            "AYARLAR",
            subtitle="MUVAHHİD yapılandırma — sunucu, model, ses, bildirimler ve arayüz.",
            status="active",
        )
        self.header.grid(row=0, column=0, sticky="ew", pady=(0, 10))

        # Summary row
        self.summary = V4HUDPanel(self.root)
        self.summary.grid(row=1, column=0, sticky="ew", pady=(0, 10))
        self.summary.frame.columnconfigure((0, 1, 2, 3, 4, 5), weight=1)
        self.t_cfg = V4StatusTile(self.summary.frame, "Yapılandırma", "BİLDİRİLMİYOR", dot=MUTED)
        self.t_cfg.grid(row=0, column=0, sticky="ew", padx=(18, 8), pady=16)
        self.t_server = V4StatusTile(self.summary.frame, "Sunucu", "BİLDİRİLMİYOR", dot=NEON_CYAN)
        self.t_server.grid(row=0, column=1, sticky="ew", padx=8, pady=16)
        self.t_model = V4StatusTile(self.summary.frame, "Model", "BİLDİRİLMİYOR", dot=NEON_CYAN)
        self.t_model.grid(row=0, column=2, sticky="ew", padx=8, pady=16)
        self.t_voice = V4StatusTile(self.summary.frame, "Ses", "BİLDİRİLMİYOR", dot=NEON_GOLD)
        self.t_voice.grid(row=0, column=3, sticky="ew", padx=8, pady=16)
        self.t_wake = V4StatusTile(self.summary.frame, "Uyandırma", "BİLDİRİLMİYOR", dot=NEON_GOLD)
        self.t_wake.grid(row=0, column=4, sticky="ew", padx=8, pady=16)
        self.t_notif = V4StatusTile(self.summary.frame, "Bildirimler", "BİLDİRİLMİYOR", dot=HOLO_GREEN)
        self.t_notif.grid(row=0, column=5, sticky="ew", padx=(8, 18), pady=16)

        # Scrollable sections
        self.scroll = ctk.CTkScrollableFrame(
            self.root,
            fg_color="transparent",
            scrollbar_button_color=CARD_BORDER,
        )
        self.scroll.grid(row=2, column=0, sticky="nsew")
        self.scroll.columnconfigure(0, weight=1)

        # Banner status
        self.banner_wrap = ctk.CTkFrame(self.scroll, fg_color="transparent")
        self.banner_wrap.pack(fill="x", padx=2, pady=(0, 10))
        self.banner_label = ctk.CTkLabel(
            self.banner_wrap,
            text="",
            text_color=TEXT,
            font=("Segoe UI", 11),
            anchor="w",
        )
        self.banner_label.pack(fill="x")
        self._show_banner("")

        self._build_general()
        self._build_server()
        self._build_model()
        self._build_voice()
        self._build_wake()
        self._build_notifications()
        self._build_ui()
        self._build_advanced()

        # Save button row
        self.save_row = ctk.CTkFrame(self.scroll, fg_color="transparent")
        self.save_row.pack(fill="x", padx=2, pady=(0, 16))
        self.save_row.columnconfigure(0, weight=1)

        self.btn_reload = ctk.CTkButton(
            self.save_row,
            text="↻ YENİDEN YÜKLE",
            width=160,
            height=40,
            fg_color=BUTTON,
            hover_color=BUTTON_HOVER,
            text_color=NEON_CYAN,
            corner_radius=10,
            border_width=1,
            border_color=CARD_BORDER,
            font=("Segoe UI", 11, "bold"),
            command=self.reload,
        )
        self.btn_reload.grid(row=0, column=1, padx=(0, 10))

        self.btn_save = ctk.CTkButton(
            self.save_row,
            text="DEĞİŞİKLİKLERİ KAYDET",
            width=220,
            height=40,
            fg_color=NEON_CYAN,
            hover_color="#00a8cc",
            text_color="#000a10",
            corner_radius=10,
            border_width=0,
            font=("Segoe UI", 11, "bold"),
            command=self.save,
        )
        self.btn_save.grid(row=0, column=2)

        try:
            self.reload()
        except Exception:
            pass

        try:
            self._sub = self.store.subscribe(lambda *_a, **_k: self._schedule_refresh())
        except Exception:
            self._sub = None

    # ── section builders ───────────────────────────────────────────────
    def _s(self, title: str, accent: str = NEON_CYAN) -> None:
        V4SectionHeader(self.scroll, title, accent=accent).pack(fill="x", padx=2, pady=(0, 8))
        p = V4HUDPanel(self.scroll)
        p.pack(fill="x", padx=2, pady=(0, 12))
        p.frame.columnconfigure(1, weight=1)
        self._cur = p.frame
        self._row = 0

    def _label(self, text: str, value_color: str = TEXT_BRIGHT) -> None:
        lbl = ctk.CTkLabel(
            self._cur,
            text=text,
            text_color=value_color,
            font=("Segoe UI", 11, "bold"),
            anchor="w",
            justify="left",
        )
        lbl.grid(row=self._row, column=0, sticky="w", padx=(18, 16), pady=(8, 4))

    def _hint(self, text: str) -> None:
        ctk.CTkLabel(
            self._cur,
            text=text,
            text_color=MUTED,
            font=("Segoe UI", 10),
            anchor="w",
            wraplength=520,
            justify="left",
        ).grid(row=self._row, column=1, sticky="w", padx=(0, 18), pady=(0, 10))
        self._row += 1

    def _entry(self, key: str, placeholder: str = "", readonly: bool = False) -> ctk.CTkEntry:
        e = ctk.CTkEntry(
            self._cur,
            height=36,
            fg_color="#040810",
            border_width=1,
            border_color=CARD_BORDER,
            corner_radius=10,
            text_color=TEXT_BRIGHT,
            placeholder_text=placeholder,
            placeholder_text_color=MUTED,
            font=("Segoe UI", 11),
            state="disabled" if readonly else "normal",
        )
        e.grid(row=self._row, column=0, columnspan=2, sticky="ew", padx=16, pady=(0, 12))
        self._inputs[key] = e
        self._row += 1
        return e

    def _checkbox(self, key: str, label: str) -> ctk.CTkCheckBox:
        var = ctk.StringVar(value="off")
        cb = ctk.CTkCheckBox(
            self._cur,
            text=label,
            variable=var,
            onvalue="on",
            offvalue="off",
            text_color=TEXT,
            font=FONT_HUD,
            fg_color=NEON_CYAN,
            hover_color="#00a8cc",
            border_color=CARD_BORDER,
            checkmark_color="#000a10",
            corner_radius=6,
        )
        cb.grid(row=self._row, column=0, columnspan=2, sticky="w", padx=18, pady=(0, 10))
        self._vars[key] = var
        self._row += 1
        return cb

    def _build_general(self) -> None:
        self._inputs: dict[str, Any] = {}
        self._vars: dict[str, ctk.StringVar] = {}
        self._s("Genel", NEON_CYAN)
        self._label("Yanıtlar")
        self._hint("Etkinleştirildiğinde kısa, öz yanıtlar tercih edilir.")
        self._checkbox("prefer_short_responses", "Kısa yanıtları tercih et")

    def _build_server(self) -> None:
        self._s("Sunucu", NEON_BLUE)
        self._label("API URL")
        self._hint("MUVAHHİD Sunucu temel URL (köprü istemcisi tarafından kullanılır).")
        self._entry("api_url", "http://host:port")

        self._label("API Anahtarı")
        self._hint("MUVAHHİD Sunucu için kimlik doğrulama anahtarı. Asla düz metin gösterilmez. Her zaman maskelenir.")
        self._entry("api_key", "●●●●●●●●●●")

    def _build_model(self) -> None:
        self._s("Model", NEON_CYAN)
        self._label("Model adı")
        self._hint("Backend veya sunucu istemcisi tarafından kullanılan birincil akıl yürütme modeli tanımlayıcısı.")
        self._entry("model", "model-id")

    def _build_voice(self) -> None:
        self._s("Ses", NEON_GOLD)
        self._checkbox("voice_enabled", "Ses sentezini etkinleştir (TTS)")

    def _build_wake(self) -> None:
        self._s("Uyandırma Kelimesi", NEON_GOLD)
        self._checkbox("wake_word_enabled", "Uyandırma kelimesi algılamayı etkinleştir")
        self._label("Uyandırma kelimesi: MUVAHHİD")
        self._hint("Kullanıcı arayüzü etiketi: MUVAHHİD. Arka plan algılama listesi (değiştirilemez): abi, akhi, dostum.")

    def _build_notifications(self) -> None:
        self._s("Bildirimler", HOLO_GREEN)
        self._checkbox("notifications_enabled", "Önemli olaylar için masaüstü bildirimlerini etkinleştir")

    def _build_ui(self) -> None:
        self._s("Arayüz", "#5aa9e6")
        self._label("Arayüz tercih ayarları")
        self._hint(
            "Mevcut sözleşmede arayüz tercih ayarları sunulmamaktadır. "
            "Görünüm modern_theme.py içinde tanımlanan premium koyu temaya sabitlenmiştir."
        )
        V4EmptyState(
            self._cur,
            "Arayüz Ayarları — SUNULMUYOR",
            subtitle="Tema, ölçek, yazı tipi yoğunluğu ve benzeri seçenekler henüz SettingsFormData parçası değildir. Bu bölüm sözleşme genişledikçe doldurulacaktır.",
            dot=MUTED,
        ).grid(row=self._row, column=0, columnspan=2, sticky="nsew", padx=14, pady=(0, 14))
        self._row += 1

    def _build_advanced(self) -> None:
        self._s("Gelişmiş", MUTED)
        self._label("Etkin kullanıcı yapılandırma yolu")
        try:
            p = user_config_path() if callable(user_config_path) else "BİLDİRİLMİYOR"
            path_str = str(p) if p is not None else "BİLDİRİLMİYOR"
        except Exception:
            path_str = "BİLDİRİLMİYOR"
        self._hint(path_str)
        self.t_cfg.set(str(Path(path_str).name if (path_str and path_str != "BİLDİRİLMİYOR") else "BİLDİRİLMİYOR"),
                    dot=NEON_CYAN if path_str != "BİLDİRİLMİYOR" else MUTED)

        self._label("Ham YAML (salt okunur, gizliler temizlenmiş)")
        self._hint("Yalnızca ekranda temizlenir; diskteki gerçek değerler korunur.")
        self.adv_textbox = ctk.CTkTextbox(
            self._cur,
            height=160,
            fg_color="#040810",
            border_width=1,
            border_color=CARD_BORDER,
            corner_radius=10,
            text_color=TEXT,
            font=("Consolas", 11),
            wrap="word",
            state="normal",
        )
        self.adv_textbox.grid(row=self._row, column=0, columnspan=2, sticky="nsew", padx=16, pady=(0, 16))
        self._row += 1
        self._load_scrubbed_yaml_into(self.adv_textbox)

    # ── helpers ────────────────────────────────────────────────────────
    def _load_scrubbed_yaml_into(self, tb: Any) -> None:
        raw_lines: list[str] = []
        try:
            if callable(user_config_path):
                p = Path(str(user_config_path()))
                if p.exists():
                    raw_lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
        except Exception:
            raw_lines = []
        if not raw_lines:
            lines = ["# Okunabilir kullanıcı yapılandırma dosyası user_config_path() konumunda bulunamadı.",
                     "# SettingsFormData tarafından varsayılanlar kullanılır."]
        else:
            lines = [_scrub_line(l) for l in raw_lines]
        tb.configure(state="normal")
        tb.delete("1.0", "end")
        tb.insert("1.0", "\n".join(lines))
        tb.configure(state="disabled")

    def _show_banner(self, text: str, color: str | None = None) -> None:
        if not text:
            self.banner_label.configure(text="", fg_color="transparent")
            try:
                self.banner_wrap.configure(fg_color="transparent", border_width=0)
            except Exception:
                pass
            return
        fg = HOLO_GREEN if color is None else color
        self.banner_label.configure(
            text=text,
            text_color="#001008" if fg in {HOLO_GREEN, NEON_CYAN, NEON_GOLD} else TEXT_BRIGHT,
        )
        self.banner_wrap.configure(fg_color=fg if fg else NEON_CYAN, border_width=1)
        try:
            self.banner_wrap.configure(fg_color=CARD, border_width=1, border_color=fg)
            self.banner_label.configure(text_color=fg if fg else NEON_CYAN)
        except Exception:
            pass

    def _set_text(self, widget: Any, value: str) -> None:
        try:
            widget.configure(state="normal")
            widget.delete(0, "end")
            widget.insert(0, value or "")
        except Exception:
            pass

    def _get_text(self, widget: Any) -> str:
        try:
            return widget.get().strip()
        except Exception:
            return ""

    def _is_on(self, key: str) -> bool:
        v = self._vars.get(key)
        if v is None:
            return False
        try:
            return str(v.get()).lower() in {"1", "true", "on", "yes"}
        except Exception:
            return False

    def _set_boolvar(self, key: str, value: bool) -> None:
        var = self._vars.get(key)
        if var is None:
            return
        try:
            var.set("on" if bool(value) else "off")
        except Exception:
            pass

    # ── Load / Save ────────────────────────────────────────────────────
    def reload(self) -> None:
        data: Any = None
        try:
            if callable(load_settings_form):
                data = load_settings_form()
        except Exception:
            data = None

        if data is None or not hasattr(data, "api_url"):
            self.t_server.set("BİLDİRİLMİYOR", dot=MUTED)
            self.t_model.set("BİLDİRİLMİYOR", dot=MUTED)
            self.t_voice.set("BİLDİRİLMİYOR", dot=MUTED)
            self.t_wake.set("BİLDİRİLMİYOR", dot=MUTED)
            self.t_notif.set("BİLDİRİLMİYOR", dot=MUTED)
            return

        api_url = str(getattr(data, "api_url", "") or "")
        api_key = str(getattr(data, "api_key", "") or "")
        model = str(getattr(data, "model", "") or "")
        prefer = bool(getattr(data, "prefer_short_responses", False))
        voice = bool(getattr(data, "voice_enabled", False))
        wake = bool(getattr(data, "wake_word_enabled", False))
        notif = bool(getattr(data, "notifications_enabled", False))

        self._set_text(self._inputs["api_url"], api_url)
        self._set_text(self._inputs["api_key"], _mask(api_key, keep=2))
        self._set_text(self._inputs["model"], model)
        self._set_boolvar("prefer_short_responses", prefer)
        self._set_boolvar("voice_enabled", voice)
        self._set_boolvar("wake_word_enabled", wake)
        self._set_boolvar("notifications_enabled", notif)

        self.t_server.set(api_url[:32] + ("…" if len(api_url) > 32 else "") or "BOŞ",
                         dot=NEON_CYAN if api_url else MUTED)
        self.t_model.set(model or "BOŞ", dot=NEON_CYAN if model else MUTED)
        self.t_voice.set("ETKİN" if voice else "PASİF", dot=NEON_GOLD if voice else MUTED)
        self.t_wake.set("ETKİN" if wake else "PASİF", dot=NEON_GOLD if wake else MUTED)
        self.t_notif.set("ETKİN" if notif else "PASİF", dot=HOLO_GREEN if notif else MUTED)

        self.header.set_status("active", "YÜKLENDİ")
        self._show_banner("Ayarlar yüklendi.", HOLO_GREEN)

    def save(self) -> None:
        if not callable(save_settings_form) or SettingsFormData is None:
            self._show_banner("save_settings_form bu yapıda kullanılamıyor — kayıt yapılamaz.", NEON_MAGENTA)
            return

        # Re-read current form to get any unsaved non-secret text entries unchanged
        existing: Any = None
        try:
            if callable(load_settings_form):
                existing = load_settings_form()
        except Exception:
            existing = None

        api_url = self._get_text(self._inputs["api_url"])
        # API key: treat as unchanged if still masked.
        key_entry_val = self._get_text(self._inputs["api_key"])
        key_changed = bool(key_entry_val) and not all(c == "●" for c in key_entry_val if c != " ") and "●" not in (getattr(existing, "api_key", "") or "") or False
        # simpler heuristic: if value contains at least one non-● char AND has fewer ● than mask length → treat as user typed.
        mask_dots = key_entry_val.count("●")
        typed_len = len(key_entry_val) - mask_dots
        if typed_len >= 2 and len(key_entry_val) >= 4 and mask_dots == 0:
            api_key = key_entry_val
        elif typed_len > 2:
            # typed new value (user pasted a key after deleting mask) → accept
            api_key = key_entry_val
        else:
            api_key = str(getattr(existing, "api_key", "") or "") if existing is not None else ""

        model = self._get_text(self._inputs["model"]) or str(getattr(existing, "model", "") or "")

        new_data = SettingsFormData(
            api_url=api_url or str(getattr(existing, "api_url", "") or "") if existing else api_url,
            api_key=api_key,
            model=model or str(getattr(existing, "model", "hermes-agent") or "hermes-agent"),
            prefer_short_responses=self._is_on("prefer_short_responses"),
            voice_enabled=self._is_on("voice_enabled"),
            wake_word_enabled=self._is_on("wake_word_enabled"),
            notifications_enabled=self._is_on("notifications_enabled"),
        )

        errors: list[str] = []
        if not new_data.api_url:
            errors.append("API URL zorunludur.")
        if errors:
            self._show_banner("Kaydedilemedi: " + " ".join(errors), NEON_MAGENTA)
            return
        try:
            res = save_settings_form(new_data)
            ok = bool(getattr(res, "ok", True)) if res is not None else True
            messages: list[str] = []
            if res is not None:
                for attr in ("errors", "warnings"):
                    v = getattr(res, attr, None)
                    if isinstance(v, list):
                        messages.extend([str(x) for x in v])
            if ok:
                self.reload()
                self._load_scrubbed_yaml_into(self.adv_textbox)
                self._show_banner("Ayarlar kaydedildi.", HOLO_GREEN)
            else:
                self._show_banner("Kayıt başarısız: " + " ".join(messages) or "Bilinmeyen hata.", NEON_MAGENTA)
        except Exception as exc:
            self._show_banner(f"Kayıt başarısız: {exc}", NEON_MAGENTA)

    # ── Lifecycle ──────────────────────────────────────────────────────
    def _schedule_refresh(self) -> None:  # pragma: no cover
        pass  # Settings changes are manual reload/save only — no timer needed.

    def destroy(self) -> None:  # pragma: no cover - lifecycle
        for t in list(self._timers):
            try:
                self.root.after_cancel(t)
            except Exception:
                pass
        self._timers.clear()
        if self._sub is not None:
            try:
                unsub = self._sub
                if callable(unsub):
                    unsub()
            except Exception:
                pass
            self._sub = None
        try:
            self.root.destroy()
        except Exception:
            pass

    def __del__(self) -> None:  # pragma: no cover - lifecycle
        try:
            self.destroy()
        except Exception:
            pass
