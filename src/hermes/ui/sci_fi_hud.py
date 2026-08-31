"""Sci-fi HUD renderer — JARVIS / holographic interface visuals."""

from __future__ import annotations

import math
from typing import Any

from hermes.ui.modern_theme import (
    GRID,
    HOLO_GREEN,
    MAGENTA,
    NEON_BLUE,
    NEON_CYAN,
    NEON_GOLD,
)
from hermes.ui.state import ActivityMode


class SciFiHudRenderer:
    """Draws the center neural-core visualizer on a tk Canvas."""

    def __init__(self) -> None:
        self._tick = 0.0
        self._particles: list[dict[str, float]] = []
        self._orbits: list[dict[str, float]] = [
            {"angle": i * (math.tau / 6), "speed": 0.018 + i * 0.004, "dist": 118 + i * 8}
            for i in range(6)
        ]

    def advance(self, dt: float = 0.12) -> None:
        self._tick += dt

    def spawn_particles(self, energy: float) -> None:
        import random

        if energy < 0.15:
            self._particles = [p for p in self._particles if p.get("life", 0) > 0]
            return
        cap = int(16 + energy * 36)
        while len(self._particles) < cap and random.random() < 0.35 + energy * 0.5:
            self._particles.append(
                {
                    "angle": random.uniform(0, math.tau),
                    "dist": random.uniform(24, 52),
                    "speed": random.uniform(1.2, 3.8) + energy * 3,
                    "life": random.uniform(0.5, 1.6),
                    "size": random.uniform(1.5, 3.5 + energy * 4),
                    "hue": random.choice(("cyan", "magenta", "gold")),
                }
            )
        for particle in self._particles:
            particle["dist"] += particle["speed"]
            particle["life"] -= 0.06
        self._particles = [p for p in self._particles if p.get("life", 0) > 0]

    def draw(
        self,
        canvas: Any,
        *,
        activity: ActivityMode,
        audio_level: float,
        bg: str,
    ) -> None:
        canvas.delete("all")
        width = max(canvas.winfo_width(), 200)
        height = max(canvas.winfo_height(), 200)
        cx, cy = width // 2, height // 2
        primary, secondary, energy = self._palette(activity)
        audio = max(energy, audio_level)
        max_r = min(width, height) // 2 - 12

        self._draw_grid(canvas, width, height, bg)
        self._draw_scanlines(canvas, width, height)
        self._draw_ripples(canvas, cx, cy, primary, audio)
        self._draw_radar(canvas, cx, cy, max_r, primary, audio)
        self._draw_hex_frame(canvas, cx, cy, 148 + audio * 12, primary, secondary)
        self._draw_arc_segments(canvas, cx, cy, max_r - 8, primary, secondary, audio)
        self._draw_rings(canvas, cx, cy, primary, secondary, audio, activity)
        self._draw_orbiters(canvas, cx, cy, primary, secondary, audio)
        self._draw_particles(canvas, cx, cy, primary, secondary)
        self._draw_waveform(canvas, cx, cy, primary, audio)
        self._draw_core(canvas, cx, cy, primary, secondary, audio)
        self._draw_reticle(canvas, cx, cy, max_r, primary)
        self._draw_corner_brackets(canvas, width, height, primary)

    def _palette(self, mode: ActivityMode) -> tuple[str, str, float]:
        palettes = {
            ActivityMode.IDLE: (NEON_CYAN, NEON_BLUE, 0.28),
            ActivityMode.LISTENING: (HOLO_GREEN, "#00cc66", 0.9),
            ActivityMode.THINKING: (NEON_GOLD, "#ff9500", 0.68),
            ActivityMode.SPEAKING: (MAGENTA, "#c9184a", 0.82),
            ActivityMode.EXECUTING: (NEON_BLUE, NEON_CYAN, 0.75),
            ActivityMode.AWAITING_APPROVAL: ("#ff6b35", NEON_GOLD, 0.5),
        }
        return palettes.get(mode, (NEON_CYAN, NEON_BLUE, 0.28))

    def _draw_grid(self, canvas: Any, width: int, height: int, bg: str) -> None:
        canvas.create_rectangle(0, 0, width, height, fill=bg, outline="")
        step = 28
        for x in range(0, width + 1, step):
            canvas.create_line(x, 0, x, height, fill=GRID, width=1)
        for y in range(0, height + 1, step):
            canvas.create_line(0, y, width, y, fill=GRID, width=1)
        # perspective vanishing lines
        canvas.create_line(0, height, width // 2, height // 3, fill=GRID, width=1)
        canvas.create_line(width, height, width // 2, height // 3, fill=GRID, width=1)

    def _draw_scanlines(self, canvas: Any, width: int, height: int) -> None:
        y = int((self._tick * 18) % max(height, 1))
        canvas.create_line(0, y, width, y, fill="#0a1628", width=2)
        canvas.create_line(0, (y + height // 3) % height, width, (y + height // 3) % height, fill="#081018", width=1)

    def _draw_ripples(self, canvas: Any, cx: int, cy: int, color: str, audio: float) -> None:
        if audio < 0.25:
            return
        for index in range(4):
            phase = (self._tick * 28 + index * 42) % 180
            canvas.create_oval(
                cx - phase,
                cy - phase,
                cx + phase,
                cy + phase,
                outline=color,
                width=1,
                dash=(4, 10),
            )

    def _draw_radar(self, canvas: Any, cx: int, cy: int, radius: int, color: str, audio: float) -> None:
        angle = self._tick * (0.6 + audio * 0.4)
        x2 = cx + math.cos(angle) * radius
        y2 = cy + math.sin(angle) * radius
        canvas.create_line(cx, cy, x2, y2, fill=color, width=2)
        for trail in range(1, 5):
            ta = angle - trail * 0.12
            tx = cx + math.cos(ta) * radius * (1 - trail * 0.04)
            ty = cy + math.sin(ta) * radius * (1 - trail * 0.04)
            canvas.create_line(cx, cy, tx, ty, fill=GRID, width=1)

    def _draw_hex_frame(
        self,
        canvas: Any,
        cx: int,
        cy: int,
        radius: float,
        primary: str,
        secondary: str,
    ) -> None:
        points: list[float] = []
        for index in range(6):
            angle = math.pi / 6 + index * (math.tau / 6) + self._tick * 0.02
            points.extend(
                (
                    cx + math.cos(angle) * radius,
                    cy + math.sin(angle) * radius,
                )
            )
        canvas.create_polygon(*points, outline=secondary, fill="", width=2)
        inner_r = radius * 0.72
        inner_pts: list[float] = []
        for index in range(6):
            angle = math.pi / 6 + index * (math.tau / 6) - self._tick * 0.015
            inner_pts.extend((cx + math.cos(angle) * inner_r, cy + math.sin(angle) * inner_r))
        canvas.create_polygon(*inner_pts, outline=primary, fill="", width=1, dash=(6, 8))

    def _draw_arc_segments(
        self,
        canvas: Any,
        cx: int,
        cy: int,
        radius: int,
        primary: str,
        secondary: str,
        audio: float,
    ) -> None:
        segments = 12
        for index in range(segments):
            start = math.degrees(self._tick * 0.04 + index * (math.tau / segments))
            extent = 18 + audio * 8
            color = primary if index % 2 == 0 else secondary
            canvas.create_arc(
                cx - radius,
                cy - radius,
                cx + radius,
                cy + radius,
                start=start,
                extent=extent,
                style="arc",
                outline=color,
                width=2 if index % 3 == 0 else 1,
            )

    def _draw_rings(
        self,
        canvas: Any,
        cx: int,
        cy: int,
        primary: str,
        secondary: str,
        audio: float,
        activity: ActivityMode,
    ) -> None:
        pulse_speed = {
            ActivityMode.LISTENING: 0.48,
            ActivityMode.SPEAKING: 0.4,
            ActivityMode.THINKING: 0.32,
            ActivityMode.EXECUTING: 0.36,
            ActivityMode.AWAITING_APPROVAL: 0.2,
            ActivityMode.IDLE: 0.16,
        }.get(activity, 0.16)
        pulse = (10 + audio * 22) * math.sin(self._tick * pulse_speed)
        colors = (primary, secondary, primary, secondary, GRID)
        radii = (142, 118, 94, 68, 44)
        for index, base_r in enumerate(radii):
            wobble = math.sin(self._tick * (1.1 + audio) + index * 0.9) * audio * 7
            grow = pulse if index < 2 else pulse * 0.35
            r = base_r + grow * (1 - index * 0.1) + wobble
            width_px = 3 if index == 0 else 2 if index < 3 else 1
            dash = () if index < 2 else (3, 9)
            canvas.create_oval(
                cx - r,
                cy - r,
                cx + r,
                cy + r,
                outline=colors[index % len(colors)],
                width=width_px,
                dash=dash,
            )
            if index == 0:
                for glow in (6, 12, 18):
                    canvas.create_oval(
                        cx - r - glow,
                        cy - r - glow,
                        cx + r + glow,
                        cy + r + glow,
                        outline=GRID,
                        width=1,
                    )

    def _draw_orbiters(
        self,
        canvas: Any,
        cx: int,
        cy: int,
        primary: str,
        secondary: str,
        audio: float,
    ) -> None:
        for orbiter in self._orbits:
            orbiter["angle"] += orbiter["speed"] * (1 + audio)
            dist = orbiter["dist"] + math.sin(self._tick + orbiter["angle"]) * 4
            ox = cx + math.cos(orbiter["angle"]) * dist
            oy = cy + math.sin(orbiter["angle"]) * dist
            size = 3 + audio * 2
            canvas.create_oval(ox - size, oy - size, ox + size, oy + size, fill=primary, outline=secondary)

    def _draw_particles(
        self,
        canvas: Any,
        cx: int,
        cy: int,
        primary: str,
        secondary: str,
    ) -> None:
        color_map = {"cyan": primary, "magenta": MAGENTA, "gold": NEON_GOLD}
        for particle in self._particles:
            px = cx + math.cos(particle["angle"]) * particle["dist"]
            py = cy + math.sin(particle["angle"]) * particle["dist"]
            size = particle["size"]
            fill = color_map.get(str(particle.get("hue")), primary)
            canvas.create_oval(px - size, py - size, px + size, py + size, fill=fill, outline=secondary)

    def _draw_waveform(self, canvas: Any, cx: int, cy: int, primary: str, audio: float) -> None:
        wave_amp = 8 + audio * 28
        wave_w = min(120, 52 + int(audio * 40))
        points: list[float] = []
        for step, x in enumerate(range(-wave_w, wave_w + 1, 6)):
            y = cy + math.sin(self._tick * (0.55 + audio * 0.3) + step * 0.5) * wave_amp
            points.extend((cx + x, y))
        if len(points) >= 4:
            canvas.create_line(*points, fill=primary, width=2, smooth=True)
            points2 = [cy + (y - cy) * 0.35 for y in points[1::2]]
            flat: list[float] = []
            for index, x in enumerate(points[::2]):
                flat.extend((x, points2[index]))
            if len(flat) >= 4:
                canvas.create_line(*flat, fill=NEON_BLUE, width=1, smooth=True)

    def _draw_core(
        self,
        canvas: Any,
        cx: int,
        cy: int,
        primary: str,
        secondary: str,
        audio: float,
    ) -> None:
        core = 12 + audio * 14
        for layer, expand in enumerate((16, 10, 5)):
            alpha_color = GRID if layer else primary
            r = core + expand + math.sin(self._tick * 2 + layer) * 2
            canvas.create_oval(cx - r, cy - r, cx + r, cy + r, outline=alpha_color, width=1)
        canvas.create_oval(
            cx - core,
            cy - core,
            cx + core,
            cy + core,
            fill=primary,
            outline=secondary,
            width=2,
        )
        canvas.create_oval(cx - 3, cy - 3, cx + 3, cy + 3, fill="#ffffff", outline="")

    def _draw_reticle(self, canvas: Any, cx: int, cy: int, radius: int, color: str) -> None:
        gap = 18
        length = 22
        for dx, dy in ((0, -1), (0, 1), (-1, 0), (1, 0)):
            x1 = cx + dx * gap
            y1 = cy + dy * gap
            x2 = cx + dx * (gap + length)
            y2 = cy + dy * (gap + length)
            canvas.create_line(x1, y1, x2, y2, fill=color, width=2)
        canvas.create_oval(
            cx - radius,
            cy - radius,
            cx + radius,
            cy + radius,
            outline=color,
            width=1,
            dash=(2, 14),
        )

    def _draw_corner_brackets(self, canvas: Any, width: int, height: int, color: str) -> None:
        pad = 10
        arm = 22
        corners = (
            (pad, pad, pad + arm, pad, pad, pad + arm),
            (width - pad, pad, width - pad - arm, pad, width - pad, pad + arm),
            (pad, height - pad, pad + arm, height - pad, pad, height - pad - arm),
            (width - pad, height - pad, width - pad - arm, height - pad, width - pad, height - pad - arm),
        )
        for x1, y1, x2, y2, x3, y3 in corners:
            canvas.create_line(x1, y1, x2, y2, fill=color, width=2)
            canvas.create_line(x1, y1, x3, y3, fill=color, width=2)
