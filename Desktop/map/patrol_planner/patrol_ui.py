#!/usr/bin/env python3
"""巡逻路径规划简易 UI（Tkinter）。"""
from __future__ import annotations

import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from PIL import Image, ImageTk

from patrol_core import TOOL_VERSION, compose_marking_preview, snap_patrol_click
from patrol_runner import (
    PatrolRunOptions,
    list_building_floors,
    list_map_yamls,
    load_mark_context,
    load_mark_contexts_for_options,
    load_switcher_config,
    resolve_switcher_path,
    run_patrol,
)

_TOOL_ROOT = Path(__file__).resolve().parent
_DEFAULT_MAP_DIR = _TOOL_ROOT.parent.parent / "ros_ws" / "install" / "rt_robot_nav2" / "share" / "rt_robot_nav2" / "map"
_DEFAULT_OUT_DIR = _TOOL_ROOT.parent / "patrol_out"


class PatrolUI(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title(f"NovaJoy 巡逻路径规划 v{TOOL_VERSION}")
        self.minsize(1024, 680)
        self._photo: ImageTk.PhotoImage | None = None
        self._overlay_paths: list[Path] = []
        self._preview_index = 0
        self._running = False

        # 手动标记
        self._mark_contexts: list = []
        self._mark_index = 0
        self._manual_points: dict[str, list[tuple[int, int]]] = {}
        self._marking_active = False
        self._map_preview_active = False
        self._display_meta: dict = {}

        self._build_widgets()
        self._refresh_map_list()

    def _build_widgets(self) -> None:
        paned = ttk.Panedwindow(self, orient=tk.HORIZONTAL)
        paned.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)

        left = ttk.Frame(paned, padding=4)
        right = ttk.Frame(paned, padding=4)
        paned.add(left, weight=1)
        paned.add(right, weight=2)

        row = 0
        ttk.Label(left, text="生成方式").grid(row=row, column=0, sticky=tk.W, pady=2)
        self.gen_var = tk.StringVar(value="auto")
        gf = ttk.Frame(left)
        gf.grid(row=row, column=1, columnspan=2, sticky=tk.EW, pady=2)
        ttk.Radiobutton(gf, text="自动规划", value="auto", variable=self.gen_var, command=self._on_gen_change).pack(side=tk.LEFT)
        ttk.Radiobutton(gf, text="手动转圈点", value="manual", variable=self.gen_var, command=self._on_gen_change).pack(side=tk.LEFT, padx=(8, 0))

        row += 1
        ttk.Label(left, text="范围").grid(row=row, column=0, sticky=tk.W, pady=2)
        self.scope_var = tk.StringVar(value="single")
        sf = ttk.Frame(left)
        sf.grid(row=row, column=1, columnspan=2, sticky=tk.EW, pady=2)
        ttk.Radiobutton(sf, text="单图（蓝点）", value="single", variable=self.scope_var, command=self._on_scope_change).pack(side=tk.LEFT)
        ttk.Radiobutton(sf, text="全楼（红点）", value="building", variable=self.scope_var, command=self._on_scope_change).pack(side=tk.LEFT, padx=(8, 0))

        row += 1
        ttk.Label(left, text="地图 yaml").grid(row=row, column=0, sticky=tk.W, pady=2)
        self.map_combo = ttk.Combobox(left, state="readonly")
        self.map_combo.grid(row=row, column=1, sticky=tk.EW, pady=2)
        self.map_combo.bind("<<ComboboxSelected>>", self._on_map_combo_change)

        row += 1
        ttk.Label(left, text="标记楼层").grid(row=row, column=0, sticky=tk.W, pady=2)
        self.floor_mark_combo = ttk.Combobox(left, state="disabled", width=28)
        self.floor_mark_combo.grid(row=row, column=1, sticky=tk.EW, pady=2)
        self.floor_mark_combo.bind("<<ComboboxSelected>>", self._on_floor_mark_selected)

        row += 1
        ttk.Label(left, text="地图目录").grid(row=row, column=0, sticky=tk.W, pady=2)
        self.map_dir_var = tk.StringVar(value=str(_DEFAULT_MAP_DIR))
        ttk.Entry(left, textvariable=self.map_dir_var).grid(row=row, column=1, sticky=tk.EW, pady=2)
        ttk.Button(left, text="浏览", command=self._browse_map_dir).grid(row=row, column=2, padx=(4, 0))

        row += 1
        ttk.Label(left, text="输出目录").grid(row=row, column=0, sticky=tk.W, pady=2)
        self.out_dir_var = tk.StringVar(value=str(_DEFAULT_OUT_DIR))
        ttk.Entry(left, textvariable=self.out_dir_var).grid(row=row, column=1, sticky=tk.EW, pady=2)
        ttk.Button(left, text="浏览", command=self._browse_out_dir).grid(row=row, column=2, padx=(4, 0))

        row += 1
        ttk.Label(left, text="switcher").grid(row=row, column=0, sticky=tk.W, pady=2)
        self.switcher_var = tk.StringVar(value="")
        ttk.Entry(left, textvariable=self.switcher_var).grid(row=row, column=1, sticky=tk.EW, pady=2)
        ttk.Button(left, text="浏览", command=self._browse_switcher).grid(row=row, column=2, padx=(4, 0))

        row += 1
        self.cov_row = ttk.Frame(left)
        self.cov_row.grid(row=row, column=0, columnspan=3, sticky=tk.EW, pady=2)
        ttk.Label(self.cov_row, text="覆盖模式").pack(side=tk.LEFT)
        self.coverage_var = tk.StringVar(value="full_free")
        ttk.Combobox(
            self.cov_row,
            textvariable=self.coverage_var,
            state="readonly",
            values=("full_free", "corridor_priority"),
            width=20,
        ).pack(side=tk.LEFT, padx=(8, 0))

        row += 1
        self.auto_frame = ttk.LabelFrame(left, text="自动规划参数", padding=4)
        self.auto_frame.grid(row=row, column=0, columnspan=3, sticky=tk.EW, pady=4)
        ttk.Label(self.auto_frame, text="未覆盖容差").grid(row=0, column=0, sticky=tk.W)
        self.uncovered_var = tk.StringVar(value="0")
        ttk.Entry(self.auto_frame, textvariable=self.uncovered_var, width=10).grid(row=0, column=1, sticky=tk.W)
        ttk.Label(self.auto_frame, text="采样步长(m)").grid(row=1, column=0, sticky=tk.W)
        self.sample_var = tk.StringVar(value="0.25")
        ttk.Entry(self.auto_frame, textvariable=self.sample_var, width=10).grid(row=1, column=1, sticky=tk.W)
        self.auto_frame.columnconfigure(1, weight=1)

        row += 1
        self.manual_hint = ttk.Label(
            left,
            text="左键添加「转圈点 S*」：车到该处原地 360° 扫描；右键删最近点。"
            "绿区=当前各点转圈可见范围（实时预览）。",
            wraplength=320,
            foreground="#555",
        )
        self.manual_hint.grid(row=row, column=0, columnspan=3, sticky=tk.W, pady=(0, 4))

        row += 1
        btn_frame = ttk.Frame(left)
        btn_frame.grid(row=row, column=0, columnspan=3, sticky=tk.EW, pady=(4, 4))
        self.load_mark_btn = ttk.Button(btn_frame, text="预览地图", command=self._on_load_mark)
        self.load_mark_btn.pack(side=tk.LEFT)
        self.clear_pts_btn = ttk.Button(btn_frame, text="清空转圈点", command=self._on_clear_points)
        self.clear_pts_btn.pack(side=tk.LEFT, padx=(8, 0))
        self.run_btn = ttk.Button(btn_frame, text="生成转圈路线", command=self._on_run)
        self.run_btn.pack(side=tk.LEFT, padx=(8, 0))

        row += 1
        btn2 = ttk.Frame(left)
        btn2.grid(row=row, column=0, columnspan=3, sticky=tk.EW)
        ttk.Button(btn2, text="刷新地图列表", command=self._refresh_map_list).pack(side=tk.LEFT)
        ttk.Button(btn2, text="打开输出目录", command=self._open_out_dir).pack(side=tk.LEFT, padx=(8, 0))

        row += 1
        ttk.Label(left, text="日志").grid(row=row, column=0, sticky=tk.NW, pady=(8, 2))
        self.log_text = tk.Text(left, height=12, width=44, wrap=tk.WORD)
        self.log_text.grid(row=row + 1, column=0, columnspan=3, sticky=tk.NSEW)
        left.rowconfigure(row + 1, weight=1)
        left.columnconfigure(1, weight=1)

        preview_bar = ttk.Frame(right)
        preview_bar.pack(fill=tk.X, pady=(0, 4))
        ttk.Label(preview_bar, text="预览").pack(side=tk.LEFT)
        self.prev_btn = ttk.Button(preview_bar, text="◀", width=3, command=self._preview_prev)
        self.prev_btn.pack(side=tk.LEFT, padx=(8, 0))
        self.preview_label = ttk.Label(preview_bar, text="—", width=16, anchor=tk.CENTER)
        self.preview_label.pack(side=tk.LEFT)
        self.next_btn = ttk.Button(preview_bar, text="▶", width=3, command=self._preview_next)
        self.next_btn.pack(side=tk.LEFT)
        self.preview_combo = ttk.Combobox(preview_bar, state="readonly", width=36)
        self.preview_combo.pack(side=tk.LEFT, padx=(8, 0), fill=tk.X, expand=True)
        self.preview_combo.bind("<<ComboboxSelected>>", self._on_preview_combo)

        self.canvas = tk.Canvas(right, bg="#222222", highlightthickness=0)
        self.canvas.pack(fill=tk.BOTH, expand=True)
        self.canvas.bind("<Configure>", lambda _e: self._redraw_canvas())
        self.canvas.bind("<Button-1>", self._on_canvas_click)
        self.canvas.bind("<Button-3>", self._on_canvas_right_click)

        self._on_gen_change()
        self._on_scope_change()

    def _append_log(self, line: str) -> None:
        self.log_text.insert(tk.END, line + "\n")
        self.log_text.see(tk.END)

    def _is_manual(self) -> bool:
        return self.gen_var.get() == "manual"

    def _on_gen_change(self) -> None:
        manual = self._is_manual()
        if manual:
            self.auto_frame.grid_remove()
            self.cov_row.grid_remove()
            self.manual_hint.grid()
            self.load_mark_btn.configure(state=tk.NORMAL, text="加载地图")
            self.clear_pts_btn.configure(state=tk.NORMAL)
            self.run_btn.configure(text="生成转圈路线")
        else:
            self.auto_frame.grid()
            self.cov_row.grid()
            self.manual_hint.grid_remove()
            self.load_mark_btn.configure(state=tk.NORMAL, text="预览地图")
            self.clear_pts_btn.configure(state=tk.DISABLED)
            self.run_btn.configure(text="生成巡逻路线")
            self._marking_active = False
        self._on_scope_change()
        if manual and self._map_preview_active:
            self._marking_active = True
        if self._map_preview_active:
            self._redraw_canvas()

    def _on_scope_change(self) -> None:
        single = self.scope_var.get() == "single"
        manual = self._is_manual()
        self.map_combo.configure(state="readonly" if single else "disabled")
        if manual and not single:
            self.floor_mark_combo.configure(state="readonly")
        else:
            self.floor_mark_combo.configure(state="disabled")
        if self._map_preview_active:
            self._on_load_mark(silent=True)

    def _browse_map_dir(self) -> None:
        path = filedialog.askdirectory(initialdir=self.map_dir_var.get())
        if path:
            self.map_dir_var.set(path)
            self._refresh_map_list()

    def _browse_out_dir(self) -> None:
        path = filedialog.askdirectory(initialdir=self.out_dir_var.get())
        if path:
            self.out_dir_var.set(path)

    def _browse_switcher(self) -> None:
        path = filedialog.askopenfilename(
            title="选择 switcher_node.py",
            filetypes=[("Python", "*.py"), ("All", "*.*")],
        )
        if path:
            self.switcher_var.set(path)

    def _open_out_dir(self) -> None:
        out = Path(self.out_dir_var.get())
        out.mkdir(parents=True, exist_ok=True)
        try:
            import os
            os.startfile(str(out))  # type: ignore[attr-defined]
        except Exception:
            messagebox.showinfo("输出目录", str(out.resolve()))

    def _refresh_map_list(self) -> None:
        maps = list_map_yamls(Path(self.map_dir_var.get()))
        self.map_combo["values"] = maps
        if maps:
            cur = self.map_combo.get()
            self.map_combo.set(cur if cur in maps else maps[0])

    def _parse_float(self, text: str, default: float) -> float:
        try:
            return float(text.strip())
        except ValueError:
            return default

    def _parse_optional_ratio(self, text: str) -> float | None:
        t = text.strip()
        if not t or t == "0":
            return None
        try:
            return max(0.0, min(1.0, float(t)))
        except ValueError:
            return None

    def _collect_options(self) -> PatrolRunOptions:
        return PatrolRunOptions(
            command=self.scope_var.get(),
            map_dir=Path(self.map_dir_var.get()),
            out_dir=Path(self.out_dir_var.get()),
            switcher=self.switcher_var.get().strip(),
            generation=self.gen_var.get(),
            coverage_mode=self.coverage_var.get(),
            max_uncovered_ratio=self._parse_optional_ratio(self.uncovered_var.get()),
            sample_step=self._parse_float(self.sample_var.get(), 0.25),
            single_map=self.map_combo.get(),
            manual_points={k: list(v) for k, v in self._manual_points.items()},
        )

    def _on_map_combo_change(self, _event=None) -> None:
        if self._map_preview_active and self.scope_var.get() == "single":
            self._on_load_mark(silent=True)

    def _on_load_mark(self, silent: bool = False) -> None:
        try:
            switcher_path = resolve_switcher_path(self.switcher_var.get().strip() or None)
            cfg = load_switcher_config(switcher_path)
            opts = self._collect_options()
            self._mark_contexts = load_mark_contexts_for_options(opts, cfg)
            if not self._mark_contexts:
                if not silent:
                    messagebox.showerror("加载失败", "没有可加载的地图")
                return
            if opts.command == "building":
                labels = [f"{c.key} ({c.yaml_path.name})" for c in self._mark_contexts]
                self.floor_mark_combo["values"] = labels
                self._mark_index = 0
                self.floor_mark_combo.set(labels[0])
            else:
                self.floor_mark_combo["values"] = []
                self.floor_mark_combo.set("")
                self._mark_index = 0
            for ctx in self._mark_contexts:
                self._manual_points.setdefault(ctx.key, [])
            self._map_preview_active = True
            self._marking_active = self._is_manual()
            self._overlay_paths = []
            self._preview_index = 0
            self._update_preview_chrome()
            self._redraw_canvas()
            if not silent:
                if self._is_manual():
                    self._append_log(f"已加载 {len(self._mark_contexts)} 张地图用于手动标记")
                else:
                    self._append_log(f"已预览 {len(self._mark_contexts)} 张地图（自动规划前可确认锚点 A）")
        except Exception as exc:
            if not silent:
                messagebox.showerror("加载失败", str(exc))

    def _current_mark_ctx(self):
        if not self._mark_contexts:
            return None
        idx = max(0, min(self._mark_index, len(self._mark_contexts) - 1))
        return self._mark_contexts[idx]

    def _on_floor_mark_selected(self, _event=None) -> None:
        sel = self.floor_mark_combo.get()
        for i, ctx in enumerate(self._mark_contexts):
            label = f"{ctx.key} ({ctx.yaml_path.name})"
            if label == sel:
                self._mark_index = i
                self._redraw_canvas()
                break

    def _mark_page_prev(self) -> None:
        if not self._mark_contexts:
            return
        self._mark_index = (self._mark_index - 1) % len(self._mark_contexts)
        if self.scope_var.get() == "building":
            labels = list(self.floor_mark_combo["values"])
            if labels:
                self.floor_mark_combo.set(labels[self._mark_index])
        self._redraw_canvas()

    def _mark_page_next(self) -> None:
        if not self._mark_contexts:
            return
        self._mark_index = (self._mark_index + 1) % len(self._mark_contexts)
        if self.scope_var.get() == "building":
            labels = list(self.floor_mark_combo["values"])
            if labels:
                self.floor_mark_combo.set(labels[self._mark_index])
        self._redraw_canvas()

    def _on_clear_points(self) -> None:
        ctx = self._current_mark_ctx()
        if not ctx:
            return
        self._manual_points[ctx.key] = []
        self._redraw_canvas()

    def _canvas_to_grid(self, cx: float, cy: float) -> tuple[int, int] | None:
        meta = self._display_meta
        if not meta:
            return None
        col = int((cx - meta["ox"]) / meta["scale"])
        row = int((cy - meta["oy"]) / meta["scale"])
        return col, row

    def _on_canvas_click(self, event) -> None:
        if not self._marking_active or not self._is_manual():
            return
        ctx = self._current_mark_ctx()
        if not ctx:
            return
        gr = self._canvas_to_grid(event.x, event.y)
        if gr is None:
            return
        col, row = gr
        snapped = snap_patrol_click(ctx.mapdata, col, row)
        if not snapped:
            messagebox.showwarning("标记", "请点击可活动区域内")
            return
        pts = self._manual_points.setdefault(ctx.key, [])
        if snapped not in pts:
            pts.append(snapped)
            self._redraw_canvas()

    def _on_canvas_right_click(self, event) -> None:
        if not self._marking_active or not self._is_manual():
            return
        ctx = self._current_mark_ctx()
        if not ctx:
            return
        gr = self._canvas_to_grid(event.x, event.y)
        if gr is None:
            return
        col, row = gr
        pts = self._manual_points.get(ctx.key, [])
        if not pts:
            return
        best_i = -1
        best_d = 999999
        for i, (pc, pr) in enumerate(pts):
            d = abs(pc - col) + abs(pr - row)
            if d < best_d:
                best_d = d
                best_i = i
        if best_i >= 0 and best_d <= 20:
            pts.pop(best_i)
            self._redraw_canvas()

    def _on_run(self) -> None:
        if self._running:
            return
        if self._is_manual() and not self._mark_contexts:
            messagebox.showwarning("提示", "请先「加载地图」再标记转圈点")
            return
        self._running = True
        self.run_btn.configure(state=tk.DISABLED)
        self.log_text.delete("1.0", tk.END)
        self._append_log("正在生成，请稍候…")
        opts = self._collect_options()

        def worker() -> None:
            def ui_log(line: str) -> None:
                self.after(0, lambda l=line: self._append_log(l))

            result = run_patrol(opts, log=ui_log)
            self.after(0, lambda: self._on_run_done(result))

        threading.Thread(target=worker, daemon=True).start()

    def _on_run_done(self, result) -> None:
        self._running = False
        self.run_btn.configure(state=tk.NORMAL)
        if not result.ok:
            messagebox.showerror("生成失败", result.message)
            return
        self._marking_active = False
        self._map_preview_active = False
        self._overlay_paths = result.overlay_paths
        self._preview_index = 0
        names = [p.name for p in self._overlay_paths]
        self.preview_combo["values"] = names
        if names:
            self.preview_combo.set(names[0])
        self._update_preview_chrome()
        self._redraw_canvas()
        messagebox.showinfo("完成", f"已生成 {len(result.plans)} 份路线。\n输出: {result.manifest_path}")

    def _update_preview_chrome(self) -> None:
        n = len(self._overlay_paths)
        manual_n = len(self._mark_contexts) if self._map_preview_active else 0
        if self._map_preview_active and manual_n > 1:
            self.prev_btn.configure(command=self._mark_page_prev)
            self.next_btn.configure(command=self._mark_page_next)
            self.preview_label.configure(text=f"标记 {self._mark_index + 1}/{manual_n}")
            self.prev_btn.configure(state=tk.NORMAL)
            self.next_btn.configure(state=tk.NORMAL)
        elif n > 0:
            self.prev_btn.configure(command=self._preview_prev)
            self.next_btn.configure(command=self._preview_next)
            self.preview_label.configure(text=f"结果 {self._preview_index + 1}/{n}")
            self.prev_btn.configure(state=tk.NORMAL if n > 1 else tk.DISABLED)
            self.next_btn.configure(state=tk.NORMAL if n > 1 else tk.DISABLED)
        else:
            self.preview_label.configure(text="—")
            self.prev_btn.configure(state=tk.DISABLED)
            self.next_btn.configure(state=tk.DISABLED)

    def _preview_prev(self) -> None:
        if not self._overlay_paths:
            return
        self._preview_index = (self._preview_index - 1) % len(self._overlay_paths)
        self.preview_combo.set(self._overlay_paths[self._preview_index].name)
        self._update_preview_chrome()
        self._redraw_canvas()

    def _preview_next(self) -> None:
        if not self._overlay_paths:
            return
        self._preview_index = (self._preview_index + 1) % len(self._overlay_paths)
        self.preview_combo.set(self._overlay_paths[self._preview_index].name)
        self._update_preview_chrome()
        self._redraw_canvas()

    def _on_preview_combo(self, _event=None) -> None:
        name = self.preview_combo.get()
        for i, p in enumerate(self._overlay_paths):
            if p.name == name:
                self._preview_index = i
                self._update_preview_chrome()
                self._redraw_canvas()
                break

    def _build_marking_image(self, ctx) -> Image.Image:
        pts = self._manual_points.get(ctx.key, [])
        return compose_marking_preview(ctx.mapdata, ctx.anchor_cell, pts)

    def _redraw_canvas(self) -> None:
        cw = max(self.canvas.winfo_width(), 1)
        ch = max(self.canvas.winfo_height(), 1)
        self.canvas.delete("all")

        if self._map_preview_active and self._mark_contexts:
            ctx = self._current_mark_ctx()
            if ctx:
                pts = self._manual_points.get(ctx.key, []) if self._is_manual() else []
                img = compose_marking_preview(ctx.mapdata, ctx.anchor_cell, pts)
                self._display_meta = self._blit_image(img, cw, ch)
                return

        if self._overlay_paths and 0 <= self._preview_index < len(self._overlay_paths):
            path = self._overlay_paths[self._preview_index]
            if path.is_file():
                img = Image.open(path).convert("RGB")
                self._display_meta = self._blit_image(img, cw, ch)
                return

        self._display_meta = {}
        hint = (
            "点击「预览地图」查看底图与锚点，再生成巡逻路线"
            if not self._is_manual()
            else "加载地图后点击添加转圈点，或生成后预览结果"
        )
        self.canvas.create_text(
            cw // 2,
            ch // 2,
            text=hint,
            fill="#888888",
        )

    def _blit_image(self, img: Image.Image, cw: int, ch: int) -> dict:
        iw, ih = img.size
        scale = min((cw - 8) / iw, (ch - 8) / ih)
        dw, dh = max(1, int(iw * scale)), max(1, int(ih * scale))
        shown = img.resize((dw, dh), Image.Resampling.LANCZOS)
        self._photo = ImageTk.PhotoImage(shown)
        ox = (cw - dw) // 2
        oy = (ch - dh) // 2
        self.canvas.create_image(ox, oy, image=self._photo, anchor=tk.NW)
        return {"ox": ox, "oy": oy, "scale": scale, "dw": dw, "dh": dh, "iw": iw, "ih": ih}


def main() -> None:
    app = PatrolUI()
    app.mainloop()


if __name__ == "__main__":
    main()
