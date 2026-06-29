"""
Станция сканирования бланков — ГБОУ Школа 1580.

Запуск: python main.py
"""

from __future__ import annotations

import json
import sys
import threading
import requests
import tkinter as tk
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Any, Dict, List, Optional

from PIL import Image, ImageTk

IS_WINDOWS = sys.platform == "win32"
IS_LINUX = sys.platform.startswith("linux")

if IS_WINDOWS:
    import pythoncom
    import win32com.client
else:
    pythoncom = None
    win32com = None

from blank_processor import process_scanned_blank
from qr_parser import QrPayload
from recognizer import RecognitionResult, Recognizer, evaluate_special_case

if IS_WINDOWS:
    from scanner_wia import (
        DuplexNotSupportedError,
        ScannerInfo,
        find_scanners,
        format_scanner_label,
        get_scanner_capabilities,
        scan_image_system_dialog,
        scan_sheet_sides,
    )
    from scanner_twain import (
        TwainDriver,
        TwainError,
        is_twain_available,
        get_twain_driver,
    )
else:
    from scanner_sane import (
        DuplexNotSupportedError,
        ScannerInfo,
        find_scanners,
        format_scanner_label,
        get_scanner_capabilities,
        scan_image_system_dialog,
        scan_sheet_sides,
        is_sane_available,
    )
    # Stubs for TWAIN (Windows-only)
    TwainDriver = None
    TwainError = Exception
    def is_twain_available(): return False
    def get_twain_driver(): return None
from station_integration import (
    apply_links_to_blanks,
    can_link_blanks,
    corrupted_blanks,
    count_chain_ready,
    detect_diversion,
    export_work_zip,
    format_blank_number,
    is_export_ready,
    needs_operator_attention,
    normalize_operator_number,
    pending_operator_reviews,
    resolve_blank_id,
    resolve_operator_input,
    reset_operator_session,
    run_auditorium_processing,
    session_work_id,
    status_display,
)

from scanner_hal_wrapper import HardwareScanner, HAL_AVAILABLE
from scan_logger import get_logger, log_exception, get_log_file

log = get_logger("main")


def get_resource_path(relative_path: str) -> Path:
    """Get path to resource, works for dev and PyInstaller bundle."""
    if hasattr(sys, '_MEIPASS'):
        # Running as PyInstaller bundle
        return Path(sys._MEIPASS) / relative_path
    return Path(__file__).parent / relative_path


BASE_DIR = get_resource_path(".")
CONFIG_PATH = get_resource_path("config.json")
APP_TITLE = "ГБОУ Школа 1580"


def load_config() -> dict:
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH, encoding="utf-8") as f:
            return json.load(f)
    return {}


@dataclass
class ScannedBlank:
    uid: str
    image: Image.Image
    index: int
    scan_batch_id: str = ""
    is_special: bool = False
    reasons: List[str] = field(default_factory=list)
    qr_data: Optional[str] = None
    qr_info: Optional[QrPayload] = None
    barcode_id: Optional[int] = None
    operator_blank_id: Optional[int] = None
    id_source: str = ""
    is_corrupted: bool = False
    has_markers: bool = False
    side_label: str = ""
    sheet_part: int = 1
    recognition: Optional[RecognitionResult] = None
    link_next: Optional[str] = None
    photo: Optional[ImageTk.PhotoImage] = None
    _ui_frame: Optional[ttk.Frame] = None
    _outer_frame: Optional[tk.Frame] = None

    @property
    def type_label(self) -> str:
        if self.qr_info and self.qr_info.type_label:
            return self.qr_info.type_label
        return "Тип не определён"

    @property
    def type_code(self) -> str:
        if self.qr_info and self.qr_info.type_code:
            return self.qr_info.type_code
        return ""

    @property
    def blank_number(self) -> str:
        blank_id = resolve_blank_id(self)
        return format_blank_number(blank_id) if blank_id is not None else ""


class ScannerSelectDialog(tk.Toplevel):
    """Диалог выбора подключённого сканера."""

    def __init__(self, parent: tk.Tk, scanners: Optional[List[ScannerInfo]] = None):
        super().__init__(parent)
        self.title("Выбор сканера")
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()
        self.configure(bg="#f0f2f5")

        self.selected: Optional[ScannerInfo] = None
        self._scanners: List[ScannerInfo] = list(scanners or [])
        self._labels: List[str] = []
        self._build_ui()
        self._reload_list(keep_selection=False)
        self.protocol("WM_DELETE_WINDOW", self._on_cancel)

        self.update_idletasks()
        x = parent.winfo_x() + (parent.winfo_width() - self.winfo_width()) // 2
        y = parent.winfo_y() + (parent.winfo_height() - self.winfo_height()) // 2
        self.geometry(f"+{x}+{y}")

    def _build_ui(self) -> None:
        frame = ttk.Frame(self, padding=20, style="Card.TFrame")
        frame.pack(fill=tk.BOTH, expand=True)

        ttk.Label(frame, text="Выберите сканер", style="Section.TLabel").pack(anchor=tk.W, pady=(0, 4))
        ttk.Label(
            frame,
            text="Список обновляется через WIA — только реально подключённые устройства.",
            style="Muted.TLabel",
            wraplength=420,
        ).pack(anchor=tk.W, pady=(0, 12))

        self._listbox = tk.Listbox(
            frame, width=52, height=8,
            font=("Segoe UI", 10), relief=tk.FLAT, highlightthickness=1,
            highlightbackground="#e2e8f0", bg="#ffffff",
        )
        self._listbox.pack(fill=tk.X, pady=(0, 12))
        self._listbox.bind("<Double-Button-1>", lambda _: self._on_ok())

        btn_row = ttk.Frame(frame)
        btn_row.pack(fill=tk.X)
        ttk.Button(btn_row, text="Обновить", command=self._refresh).pack(side=tk.LEFT)
        ttk.Button(btn_row, text="Отмена", command=self._on_cancel, style="Ghost.TButton").pack(side=tk.RIGHT, padx=(6, 0))
        ttk.Button(btn_row, text="Выбрать", command=self._on_ok, style="Primary.TButton").pack(side=tk.RIGHT)

        self.bind("<Return>", lambda _: self._on_ok())
        self.bind("<Escape>", lambda _: self._on_cancel())

    def _reload_list(self, keep_selection: bool = True) -> None:
        prev = self._scanners[self._listbox.curselection()[0]] if keep_selection and self._listbox.curselection() else None
        self._labels = [format_scanner_label(s) for s in self._scanners]
        self._listbox.delete(0, tk.END)
        for label in self._labels:
            self._listbox.insert(tk.END, label)
        if not self._scanners:
            return
        if prev:
            for idx, scanner in enumerate(self._scanners):
                if scanner.device_id == prev.device_id:
                    self._listbox.selection_set(idx)
                    self._listbox.see(idx)
                    return
        self._listbox.selection_set(0)

    def _refresh(self) -> None:
        self._scanners = find_scanners()
        self._reload_list(keep_selection=False)
        if not self._scanners:
            messagebox.showwarning("Сканеры", "Подключённые сканеры не найдены.", parent=self)

    def _on_ok(self) -> None:
        sel = self._listbox.curselection()
        if not sel or not self._scanners:
            messagebox.showwarning("Сканер", "Выберите сканер из списка.", parent=self)
            return
        self.selected = self._scanners[sel[0]]
        self.grab_release()
        self.destroy()

    def _on_cancel(self) -> None:
        self.selected = None
        self.grab_release()
        self.destroy()


class ManualReviewDialog(tk.Toplevel):
    """Окно поверх всего: верхняя треть бланка + ручной ввод номера оператором."""

    def __init__(
        self,
        parent: tk.Tk,
        reviews: List[Dict[str, Any]],
        blanks: List[ScannedBlank],
        on_save,
    ):
        super().__init__(parent)
        self.title("Ручной ввод номера")
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()
        self.attributes("-topmost", True)
        self.configure(bg="#f0f2f5")

        self._reviews = reviews
        self._blanks = blanks
        self._blank_by_number = {
            b.blank_number: b for b in blanks if b.blank_number
        }
        self._index = 0
        self._on_save = on_save
        self._photo: Optional[ImageTk.PhotoImage] = None

        self._build_ui()
        self._show_current()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        self.update_idletasks()
        x = parent.winfo_x() + (parent.winfo_width() - self.winfo_width()) // 2
        y = parent.winfo_y() + (parent.winfo_height() - self.winfo_height()) // 2
        self.geometry(f"+{x}+{y}")

    def _build_ui(self) -> None:
        self._frame = ttk.Frame(self, padding=16, style="Card.TFrame")
        self._frame.pack(fill=tk.BOTH, expand=True)

        self._progress = ttk.Label(self._frame, text="", style="Muted.TLabel")
        self._progress.pack(anchor=tk.W, pady=(0, 6))

        self._blank_label = ttk.Label(self._frame, text="", style="Section.TLabel", wraplength=480)
        self._blank_label.pack(anchor=tk.W, pady=(0, 4))

        self._hint_label = ttk.Label(self._frame, text="", style="Muted.TLabel", wraplength=480)
        self._hint_label.pack(anchor=tk.W, pady=(0, 8))

        self._img_label = tk.Label(self._frame, bg="#e2e8f0", width=480, height=180)
        self._img_label.pack(fill=tk.X, pady=(0, 12))

        input_row = ttk.Frame(self._frame)
        input_row.pack(fill=tk.X, pady=(0, 12))
        ttk.Label(input_row, text="Номер:", style="Section.TLabel").pack(side=tk.LEFT, padx=(0, 8))
        self._entry = ttk.Entry(input_row, font=("Consolas", 12), width=22)
        self._entry.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self._entry.bind("<Return>", lambda _: self._on_save_click())
        self._expected_len = 13

        btn_row = ttk.Frame(self._frame)
        btn_row.pack(fill=tk.X)
        ttk.Button(btn_row, text="Пропустить", command=self._on_skip, style="Ghost.TButton").pack(side=tk.LEFT)
        ttk.Button(btn_row, text="Сохранить", command=self._on_save_click, style="Primary.TButton").pack(side=tk.RIGHT)

    def _find_blank_image(self, blank_number: str) -> Optional[Image.Image]:
        normalized = normalize_operator_number(blank_number, 13) or blank_number
        blank = self._blank_by_number.get(normalized) or self._blank_by_number.get(blank_number)
        if blank and blank.image:
            return blank.image
        for b in self._blanks:
            if b.blank_number in {normalized, blank_number} and b.image:
                return b.image
        return None

    def _show_top_third(self, img: Image.Image) -> None:
        crop = img.crop((0, 0, img.width, max(1, img.height // 3)))
        max_w = 480
        ratio = min(1.0, max_w / crop.width)
        disp = crop.resize(
            (max(1, int(crop.width * ratio)), max(1, int(crop.height * ratio))),
            Image.Resampling.LANCZOS,
        )
        self._photo = ImageTk.PhotoImage(disp)
        self._img_label.config(image=self._photo, width=disp.width, height=disp.height, text="")

    def _show_current(self) -> None:
        if self._index >= len(self._reviews):
            self._on_close()
            return

        review = self._reviews[self._index]
        blank_num = str(review.get("blank", "—"))
        field = str(review.get("field", "next_blank"))
        reason = str(review.get("reason", ""))

        self._progress.config(text=f"Вопрос {self._index + 1} из {len(self._reviews)}")
        self._blank_label.config(text=f"Бланк {blank_num}")

        if field in ("page", "page_number"):
            self._hint_label.config(text="Введите номер страницы (3 цифры) с бланка")
            self._expected_len = 3
        else:
            self._hint_label.config(
                text=f"Введите верный номер следующего бланка (13 цифр). {reason}"
            )
            self._expected_len = 13

        self._entry.delete(0, tk.END)
        auto = review.get("automatic_choice") or review.get("recommended")
        if auto:
            self._entry.insert(0, str(auto))

        img = self._find_blank_image(blank_num)
        if img:
            self._show_top_third(img)
        else:
            self._photo = None
            self._img_label.config(image="", text="Изображение не найдено")

        self._entry.focus_set()

    def _on_save_click(self) -> None:
        if self._index >= len(self._reviews):
            return
        review = self._reviews[self._index]
        src = str(review.get("blank", ""))
        value = normalize_operator_number(self._entry.get(), self._expected_len)
        if not value:
            messagebox.showwarning(
                "Номер",
                f"Введите ровно {self._expected_len} цифр.",
                parent=self,
            )
            return
        review_id = str(review.get("review_id", review.get("id", ""))) or None
        self._on_save(src, value, review_id)
        self._index += 1
        self._show_current()

    def _on_skip(self) -> None:
        self._index += 1
        self._show_current()

    def _on_close(self) -> None:
        self.attributes("-topmost", False)
        self.grab_release()
        self.destroy()


class CorruptedBlankDialog(tk.Toplevel):
    """Испорченный QR: верхняя треть бланка + ввод номера текущего бланка."""

    def __init__(
        self,
        parent: tk.Tk,
        corrupted: List[ScannedBlank],
        on_save,
        on_discard,
    ):
        super().__init__(parent)
        self.title("Испорченные бланки")
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()
        self.attributes("-topmost", True)
        self.configure(bg="#f0f2f5")

        self._blanks = list(corrupted)
        self._index = 0
        self._on_save = on_save
        self._on_discard = on_discard
        self._photo: Optional[ImageTk.PhotoImage] = None

        self._build_ui()
        self._show_current()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        self.update_idletasks()
        x = parent.winfo_x() + (parent.winfo_width() - self.winfo_width()) // 2
        y = parent.winfo_y() + (parent.winfo_height() - self.winfo_height()) // 2
        self.geometry(f"+{x}+{y}")

    def _build_ui(self) -> None:
        self._frame = ttk.Frame(self, padding=16, style="Card.TFrame")
        self._frame.pack(fill=tk.BOTH, expand=True)

        self._progress = ttk.Label(self._frame, text="", style="Muted.TLabel")
        self._progress.pack(anchor=tk.W, pady=(0, 6))

        self._blank_label = ttk.Label(self._frame, text="", style="Section.TLabel", wraplength=480)
        self._blank_label.pack(anchor=tk.W, pady=(0, 4))

        self._hint_label = ttk.Label(
            self._frame,
            text="Введите номер текущего бланка (13 цифр) — под штрихкодом на бланке",
            style="Muted.TLabel",
            wraplength=480,
        )
        self._hint_label.pack(anchor=tk.W, pady=(0, 8))

        self._img_label = tk.Label(self._frame, bg="#e2e8f0", width=480, height=180)
        self._img_label.pack(fill=tk.X, pady=(0, 12))

        input_row = ttk.Frame(self._frame)
        input_row.pack(fill=tk.X, pady=(0, 12))
        ttk.Label(input_row, text="Номер:", style="Section.TLabel").pack(side=tk.LEFT, padx=(0, 8))
        self._entry = ttk.Entry(input_row, font=("Consolas", 12), width=22)
        self._entry.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self._entry.bind("<Return>", lambda _: self._on_save_click())

        btn_row = ttk.Frame(self._frame)
        btn_row.pack(fill=tk.X)
        ttk.Button(btn_row, text="Пропустить", command=self._on_skip, style="Ghost.TButton").pack(side=tk.LEFT)
        ttk.Button(btn_row, text="Не бланк", command=self._on_not_blank, style="Danger.TButton").pack(side=tk.LEFT, padx=(8, 0))
        ttk.Button(btn_row, text="Сохранить", command=self._on_save_click, style="Primary.TButton").pack(side=tk.RIGHT)

    def _show_top_third(self, img: Image.Image) -> None:
        crop = img.crop((0, 0, img.width, max(1, img.height // 3)))
        max_w = 480
        ratio = min(1.0, max_w / crop.width)
        disp = crop.resize(
            (max(1, int(crop.width * ratio)), max(1, int(crop.height * ratio))),
            Image.Resampling.LANCZOS,
        )
        self._photo = ImageTk.PhotoImage(disp)
        self._img_label.config(image=self._photo, width=disp.width, height=disp.height, text="")

    def _show_current(self) -> None:
        if self._index >= len(self._blanks):
            self._on_close()
            return

        blank = self._blanks[self._index]
        self._progress.config(text=f"Бланк {self._index + 1} из {len(self._blanks)}")
        self._blank_label.config(text=f"#{blank.index} · {blank.type_label}")

        self._entry.delete(0, tk.END)
        if blank.image:
            self._show_top_third(blank.image)
        else:
            self._photo = None
            self._img_label.config(image="", text="Изображение не найдено")

        self._entry.focus_set()

    def _on_save_click(self) -> None:
        if self._index >= len(self._blanks):
            return
        blank = self._blanks[self._index]
        value = normalize_operator_number(self._entry.get(), 13)
        if not value:
            messagebox.showwarning("Номер", "Введите ровно 13 цифр.", parent=self)
            return
        self._on_save(blank.uid, value)
        self._index += 1
        self._show_current()

    def _on_skip(self) -> None:
        self._index += 1
        self._show_current()

    def _on_not_blank(self) -> None:
        if self._index >= len(self._blanks):
            return
        blank = self._blanks[self._index]
        self._on_discard(blank.uid)
        del self._blanks[self._index]
        self._show_current()

    def _on_close(self) -> None:
        self.attributes("-topmost", False)
        self.grab_release()
        self.destroy()


class LoginDialog(tk.Toplevel):
    def __init__(self, parent):
        super().__init__(parent)

        self.result = None

        self.title("Авторизация")
        self.geometry("350x220")
        self.resizable(False, False)

        self.transient(parent)
        self.grab_set()

        ttk.Label(self, text="Логин").pack(pady=5)
        self.login = ttk.Entry(self)
        self.login.pack(fill="x", padx=20)

        ttk.Label(self, text="Пароль").pack(pady=5)
        self.password = ttk.Entry(self, show="*")
        self.password.pack(fill="x", padx=20)

        ttk.Label(self, text="Корпус").pack(pady=5)

        self.corpus = ttk.Combobox(
            self,
            values=["1", "2", "3", "4"],
            state="readonly"
        )
        self.corpus.pack(fill="x", padx=20)
        self.corpus.current(0)

        ttk.Button(
            self,
            text="Войти",
            command=self.try_login
        ).pack(pady=20)

    def try_login(self):
        login = self.login.get().strip()
        password = self.password.get()
        corpus = self.corpus.get()

        if not login or not password:
            messagebox.showerror(
                "Ошибка",
                "Введите логин и пароль",
                parent=self
            )
            return

        try:
            if int(corpus) not in [1, 2, 3, 4]:
                messagebox.showerror(
                    "Ошибка",
                    "Корпус должен быть числом от 1 до 4",
                    parent=self
                )
                return

            r = requests.post(
                "https://1580.ru/api/stantion/login",
                json={
                    "login": login,
                    "password": password,
                    "corpus": corpus
                },
                timeout=10
            )

            if r.status_code != 200:
                messagebox.showerror(
                    "Ошибка",
                    f"Ошибка сервера: {r.status_code}",
                    parent=self
                )
                return

            data = r.json()

            if data.get("success"):
                self.result = {
                    "login": login,
                    "corpus": corpus,
                    "token": data.get("token")
                }

                self.destroy()

            else:
                messagebox.showerror(
                    "Ошибка",
                    data.get("message", "Неверный логин или пароль"),
                    parent=self
                )

        except Exception as e:
            messagebox.showerror(
                "Ошибка",
                str(e),
                parent=self
            )


class ScanStationApp:
    def __init__(self) -> None:
        self.config = load_config()
        self.title = "Станция обработки"
        self.display_cfg = self.config.get("display", {})
        self.ui_cfg = self.config.get("ui", {})

        self.scanner: Optional[ScannerInfo] = None
        self._scanner_caps: dict = {}
        self._twain_driver: Optional[TwainDriver] = None
        self._use_twain: bool = False
        self._hw_scanner: Optional[HardwareScanner] = None
        self._use_hal: bool = False
        self._init_scanners()
        self.recognizer = Recognizer()
        self.blanks: List[ScannedBlank] = []
        self.zoom = float(self.display_cfg.get("initial_zoom", 1.0))
        self._scanning = False
        self._processing = False
        self.auditorium_result: Optional[Dict[str, Any]] = None
        self.diversion_active = False
        self._diversion_details: List[str] = []
        self._diversion_notified = False
        self._review_notified = False
        self._ready_notified = False
        self._selected_ids: set[str] = set()
        self._last_deleted: list[ScannedBlank] = []

        self.root = tk.Tk()
        
        icon_path = get_resource_path("scan.ico")
        if icon_path.exists():
            try:
                self.root.iconbitmap(str(icon_path))
            except tk.TclError:
                pass  # Skip icon on Linux if not supported
        self.root.title(self.title)
        self.root.minsize(1100, 640)
        self.root.configure(bg=self.ui_cfg.get("background", "#f0f2f5"))
        self._setup_styles()
        self._build_ui()

        self.root.bind(
            "<Delete>",
            lambda e: self._delete_selected()
        )
        self.root.bind(
            "<Control-a>",
            lambda e: self._select_all()
        )

        self.root.after(100, self._startup_select_scanner)
        self.root.after(200, self._maximize_window)


    def _delete_all_blanks(self):
        self._selected_ids.clear()

        if not self.blanks:
            return

        if not messagebox.askyesno(
            "Удалить все",
            f"Удалить все {len(self.blanks)} бланков?",
            parent=self.root,
        ):
            return

        self._last_deleted = self.blanks.copy()
        self._reset_session(
            confirm=False,
            status="Все бланки удалены",
        )

    
    def _on_scan_complete(self, images):
        log.info(f"_on_scan_complete() called with {len(images) if images else 0} images")
        if not images:
            log.warning("No images received, scan cancelled")
            self._set_status("Сканирование отменено")
            return

        batch_id = str(uuid.uuid4())
        log.info(f"Processing batch {batch_id}")

        results = []

        for part_idx, raw_image in enumerate(images, start=1):
            log.debug(f"Processing image {part_idx}/{len(images)}: {raw_image.size}")

            processed = process_scanned_blank(
                raw_image,
                self.config,
                sheet_part=part_idx,
            )

            if not processed.visible:
                log.debug(f"Image {part_idx} not visible, skipping")
                continue

            recognition = self.recognizer.recognize(
                processed.image,
                blank_type=(
                    processed.qr_info.type_code
                    if processed.qr_info
                    else ""
                ),
            )

            special, reasons = evaluate_special_case(
                recognition,
                self.config,
            )

            results.append(
                {
                    "image": processed.image,
                    "is_special": special,
                    "reasons": reasons,
                    "scan_batch_id": batch_id,
                    "qr_data": processed.qr_data,
                    "qr_info": processed.qr_info,
                    "barcode_id": processed.barcode_id,
                    "id_source": processed.id_source,
                    "is_corrupted": processed.is_corrupted,
                    "has_markers": processed.has_markers,
                    "process_note": processed.reason,
                    "side_label": "Лицевая",
                    "sheet_part": part_idx,
                    "recognition": recognition,
                }
            )
            log.debug(f"Image {part_idx} processed successfully")

        self._on_scan_batch_done(
            results,
            0,
            len(images),
        )


    def _setup_styles(self) -> None:
        style = ttk.Style()
        style.theme_use("clam")

        bg = self.ui_cfg.get("background", "#f0f2f5")
        sidebar = self.ui_cfg.get("sidebar", "#ffffff")
        text = self.ui_cfg.get("text", "#1e293b")
        muted = self.ui_cfg.get("muted", "#64748b")
        accent = self.ui_cfg.get("accent", "#2563eb")

        style.configure(".", font=("Segoe UI", 10), background=bg, foreground=text)
        style.configure("App.TFrame", background=bg)
        style.configure("Sidebar.TFrame", background=sidebar)
        style.configure("Card.TFrame", background=sidebar)
        style.configure("Title.TLabel", font=("Segoe UI", 13, "bold"), background=sidebar, foreground=text)
        style.configure("Section.TLabel", font=("Segoe UI", 10, "bold"), background=sidebar, foreground=text)
        style.configure("Muted.TLabel", font=("Segoe UI", 9), background=sidebar, foreground=muted)
        style.configure("Count.TLabel", font=("Segoe UI", 22, "bold"), background=sidebar, foreground=text)
        style.configure("CountSmall.TLabel", font=("Segoe UI", 17, "bold"), background=sidebar, foreground=text)
        style.configure("TypeCount.TLabel", font=("Segoe UI", 9), background=sidebar, foreground=muted)
        style.configure("Status.TLabel", font=("Segoe UI", 10, "bold"), background=sidebar)
        style.configure("Rail.TLabel", font=("Segoe UI", 9), background=bg, foreground=muted)
        style.configure("Hint.TLabel", font=("Segoe UI", 8), background=bg, foreground=muted)

        style.configure("Primary.TButton", font=("Segoe UI", 10, "bold"), padding=(12, 8), background=accent, foreground="#ffffff")
        style.map("Primary.TButton", background=[("active", "#1d4ed8"), ("disabled", "#cbd5e1")])

        style.configure("Link.TButton", font=("Segoe UI", 10, "bold"), padding=(12, 8), background="#0f766e", foreground="#ffffff")
        style.map("Link.TButton", background=[("active", "#0d9488"), ("disabled", "#cbd5e1")])

        style.configure("Ghost.TButton", font=("Segoe UI", 9), padding=(8, 5), background=sidebar, foreground=text)
        style.map("Ghost.TButton", background=[("active", "#f1f5f9")])

        style.configure("Danger.TButton", font=("Segoe UI", 9), padding=(8, 6), background=sidebar, foreground="#dc2626")
        style.map("Danger.TButton", background=[("active", "#fee2e2")])

        style.configure("Candidate.TButton", font=("Consolas", 10), padding=(10, 8), anchor=tk.W, background="#f8fafc")
        style.map("Candidate.TButton", background=[("active", "#e2e8f0")])

        style.configure("Zoom.TButton", font=("Segoe UI", 14, "bold"), width=3, padding=6)

    def _maximize_window(self) -> None:
        try:
            self.root.state("zoomed")  # Windows
        except tk.TclError:
            self.root.attributes("-zoomed", True)  # Linux

    def _notify_warning(self, title: str, message: str) -> None:
        messagebox.showwarning(title, message, parent=self.root)

    def _notify_info(self, title: str, message: str) -> None:
        messagebox.showinfo(title, message, parent=self.root)

    def _notify_error(self, title: str, message: str) -> None:
        messagebox.showerror(title, message, parent=self.root)

    def _build_ui(self) -> None:
        outer = ttk.Frame(self.root, style="App.TFrame", padding=10)
        outer.pack(fill=tk.BOTH, expand=True)
        outer.columnconfigure(1, weight=1)
        outer.rowconfigure(0, weight=1)

        # --- Левая панель (фиксированная, без скролла) ---
        left_wrap = ttk.Frame(outer, style="Card.TFrame", padding=12)
        left_wrap.grid(row=0, column=0, sticky="ns", padx=(0, 10))
        left_wrap.configure(width=270)
        left_wrap.grid_propagate(False)

        ttk.Label(left_wrap, text="ГБОУ Школа 1580", style="Title.TLabel", wraplength=228).pack(anchor=tk.W, pady=(0, 6))
        self._scanner_label = ttk.Label(left_wrap, text="Сканер не выбран", style="Muted.TLabel", wraplength=228)
        self._scanner_label.pack(anchor=tk.W, pady=(0, 8))

        self._scan_btn = ttk.Button(
            left_wrap, text="Сканировать", style="Primary.TButton",
            command=self._on_scan_click, state=tk.DISABLED,
        )
        self._scan_btn.pack(fill=tk.X, pady=(0, 10))

        ttk.Separator(left_wrap, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=(0, 8))

        counts = ttk.Frame(left_wrap, style="Sidebar.TFrame")
        counts.pack(fill=tk.X, pady=(0, 10))
        counts.columnconfigure(0, weight=1)
        counts.columnconfigure(1, weight=1)

        blanks_cell = ttk.Frame(counts, style="Sidebar.TFrame")
        blanks_cell.grid(row=0, column=0, sticky="w", padx=(0, 4))
        ttk.Label(blanks_cell, text="Бланков", style="Muted.TLabel").pack(anchor=tk.W)
        self._blanks_count_label = ttk.Label(blanks_cell, text="0", style="CountSmall.TLabel")
        self._blanks_count_label.pack(anchor=tk.W)

        links_cell = ttk.Frame(counts, style="Sidebar.TFrame")
        links_cell.grid(row=0, column=1, sticky="w", padx=(4, 0))
        ttk.Label(links_cell, text="Связей", style="Muted.TLabel").pack(anchor=tk.W)
        self._links_count_label = ttk.Label(links_cell, text="0", style="CountSmall.TLabel")
        self._links_count_label.pack(anchor=tk.W)

        self._chain_status_label = ttk.Label(
            left_wrap,
            text="Нет уведомлений",
            style="Muted.TLabel",
            foreground="#64748b",
        )
        self._chain_status_label.pack(anchor=tk.W, pady=(0, 10))

        ttk.Separator(left_wrap, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=(0, 8))

        self._review_btn = ttk.Button(
            left_wrap, text="Особые ситуации (0)", style="Ghost.TButton",
            command=self._open_manual_review, state=tk.DISABLED,
        )
        self._review_btn.pack(fill=tk.X, pady=(0, 6))

        self._corrupted_btn = ttk.Button(
            left_wrap, text="Испорченные QR (0)", style="Ghost.TButton",
            command=self._open_corrupted_review, state=tk.DISABLED,
        )
        self._corrupted_btn.pack(fill=tk.X, pady=(0, 6))

        self._link_btn = ttk.Button(
            left_wrap, text="Связать бланки", style="Link.TButton",
            command=self._on_link_click, state=tk.DISABLED,
        )
        self._link_btn.pack(fill=tk.X, pady=(0, 6))

        self._export_btn = ttk.Button(
            left_wrap, text="Экспортировать", command=self._export_zip,
            style="Primary.TButton", state=tk.DISABLED,
        )
        self._export_btn.pack(fill=tk.X, pady=(0, 8))

        aux = ttk.Frame(left_wrap, style="Sidebar.TFrame")
        aux.pack(fill=tk.X)
        aux.columnconfigure(0, weight=1)
        aux.columnconfigure(1, weight=1)
        ttk.Button(aux, text="Сканеры", command=lambda: self._select_scanner(force_dialog=True), style="Ghost.TButton").grid(
            row=0, column=0, sticky="ew", padx=(0, 3),
        )
        ttk.Button(aux, text="Сбросить", command=self._reset_session, style="Ghost.TButton").grid(
            row=0, column=1, sticky="ew", padx=(3, 0),
        )

        self._status_label = ttk.Label(left_wrap, text="Готово", style="Muted.TLabel", wraplength=228)
        self._status_label.pack(anchor=tk.W, side=tk.BOTTOM, pady=(12, 0))

        # --- Центр ---
        center = ttk.Frame(outer, style="Card.TFrame", padding=8)
        center.grid(row=0, column=1, sticky="nsew")
        center.rowconfigure(1, weight=1)
        center.columnconfigure(0, weight=1)

        header = ttk.Frame(center, style="Card.TFrame")
        header.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        ttk.Label(header, text="Отсканированные бланки", style="Section.TLabel").pack(side=tk.LEFT)

        self._canvas = tk.Canvas(center, bg="#e8edf3", highlightthickness=0, bd=0)
        v_scroll = ttk.Scrollbar(center, orient=tk.VERTICAL, command=self._canvas.yview)
        self._canvas.configure(yscrollcommand=v_scroll.set)

        self._canvas.grid(row=1, column=0, sticky="nsew")
        v_scroll.grid(row=1, column=1, sticky="ns")

        self._canvas_frame = ttk.Frame(self._canvas, style="Card.TFrame")
        self._canvas_window = self._canvas.create_window((0, 0), window=self._canvas_frame, anchor=tk.NW)
        self._canvas_frame.bind("<Configure>", self._on_frame_configure)
        self._canvas.bind("<Configure>", self._on_canvas_configure)
        self._last_canvas_width = 0

        # --- Правая панель ---
        right = ttk.Frame(outer, style="App.TFrame", padding=(10, 0, 0, 0))
        right.grid(row=0, column=2, sticky="ns")

        ttk.Label(right, text="Масштаб", style="Rail.TLabel").pack(pady=(0, 6))
        ttk.Button(right, text="+", style="Zoom.TButton", command=self._zoom_in).pack(pady=4)
        self._zoom_label = ttk.Label(right, text=f"{int(self.zoom * 100)}%", style="Rail.TLabel")
        self._zoom_label.pack(pady=4)
        ttk.Button(right, text="−", style="Zoom.TButton", command=self._zoom_out).pack(pady=4)

        ttk.Separator(right, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=(14, 10))

        self._delete_btn = ttk.Button(
            right, text="Удалить", style="Danger.TButton",
            command=self._delete_selected, state=tk.DISABLED, width=8,
        )
        self._delete_btn.pack(pady=(0, 4))

        self._undo_btn = ttk.Button(
            right,
            text="Назад",
            style="Ghost.TButton",
            command=self._undo_delete,
            width=8,
        )
        self._undo_btn.pack(pady=(0, 6))
        self._undo_btn.config(state=tk.DISABLED)

        self._selected_label = ttk.Label(right, text="Выбрано: 0", style="Hint.TLabel", wraplength=88)
        self._selected_label.pack(pady=(2, 0))

        ttk.Label(right, text="Ctrl + клик", style="Hint.TLabel").pack(pady=(6, 0))

    def _on_frame_configure(self, _event=None) -> None:
        canvas_w = max(1, self._canvas.winfo_width())
        frame_h = max(1, self._canvas_frame.winfo_reqheight())
        self._canvas.configure(scrollregion=(0, 0, canvas_w, frame_h))

    def _on_canvas_configure(self, event) -> None:
        self._canvas.itemconfig(self._canvas_window, width=event.width)
        if abs(event.width - self._last_canvas_width) >= 8 and self.blanks:
            self._last_canvas_width = event.width
            self._refresh_all_thumbnails()

    def _set_status(self, text: str) -> None:
        short = text if len(text) <= 42 else text[:39] + "…"
        self._status_label.config(text=short)

    def _update_counts(self) -> None:
        self._blanks_count_label.config(text=str(len(self.blanks)))
        links = 0
        if self.auditorium_result and self.auditorium_result.get("links"):
            links = len(self.auditorium_result["links"])
        else:
            links = sum(1 for b in self.blanks if b.link_next)
        self._links_count_label.config(text=str(links))

    def _update_chain_status(self) -> None:
        detected, details = detect_diversion(self.auditorium_result)
        self.diversion_active = detected
        self._diversion_details = details

        ready = is_export_ready(self.auditorium_result, self.blanks, self.config)

        if ready:
            self._chain_status_label.config(text="Готово", foreground="#16a34a")
        elif detected:
            self._chain_status_label.config(text="Диверсия", foreground="#dc2626")
        else:
            label, color, _extra = status_display(self.auditorium_result)
            self._chain_status_label.config(text=label, foreground=color)

        pending = pending_operator_reviews(self.auditorium_result or {})
        review_count = len(pending)
        if review_count:
            self._review_btn.config(text=f"Спорные ({review_count})", state=tk.NORMAL)
        else:
            self._review_btn.config(text="Спорные (0)", state=tk.DISABLED)

        corrupted_count = len(corrupted_blanks(self.blanks))
        if corrupted_count:
            self._corrupted_btn.config(text=f"Испорченные ({corrupted_count})", state=tk.NORMAL)
        else:
            self._corrupted_btn.config(text="Испорченные (0)", state=tk.DISABLED)

        self._refresh_blank_borders()
        self._update_action_buttons()

    def _update_action_buttons(self) -> None:
        if can_link_blanks(self.blanks) and not self._processing:
            self._link_btn.config(state=tk.NORMAL)
        else:
            self._link_btn.config(state=tk.DISABLED)

        if is_export_ready(self.auditorium_result, self.blanks, self.config):
            self._export_btn.config(state=tk.NORMAL)
        else:
            self._export_btn.config(state=tk.DISABLED)

    def _on_link_click(self) -> None:
        if not self.blanks:
            return
        if not can_link_blanks(self.blanks):
            corrupted = corrupted_blanks(self.blanks)
            if corrupted:
                self._notify_warning(
                    "Связать бланки",
                    f"Сначала введите номера для {len(corrupted)} испорченных бланков.\n"
                    "Нажмите «Испорченные» в левой панели.",
                )
            else:
                self._notify_warning(
                    "Связать бланки",
                    "Не все бланки распознаны по QR или штрихкоду.",
                )
            return
        self._run_chain_processing()

    def _init_twain(self) -> None:
        twain_cfg = self.config.get("twain", {})
        if not twain_cfg.get("enabled", True):
            return
        self._twain_driver = get_twain_driver()
        if self._twain_driver and self._twain_driver.available:
            self._use_twain = twain_cfg.get("prefer_over_wia", True)

    def _scan_twain_worker(self) -> None:
        log.info("_scan_twain_worker() START")
        try:
            if not self._twain_driver:
                log.error("TWAIN driver not initialized!")
                raise TwainError("TWAIN driver not initialized")

            settings = self.config.get("scan_settings", {})
            dpi = int(settings.get("dpi", 300))
            color_mode = settings.get("color_mode", "grayscale")
            duplex = bool(settings.get("duplex", True))

            log.info(f"TWAIN scan: dpi={dpi}, color={color_mode}, duplex={duplex}")

            def on_progress(msg: str) -> None:
                log.debug(f"TWAIN progress: {msg}")
                self.root.after(0, lambda: self._set_status(msg))

            raw_images = self._twain_driver.scan(
                dpi=dpi,
                duplex=duplex,
                color_mode=color_mode,
                on_progress=on_progress,
            )

            log.info(f"_scan_twain_worker() got {len(raw_images)} images")
            self.root.after(0, lambda: self._on_scan_complete(raw_images))

        except TwainError as exc:
            log.error(f"TwainError: {exc}")
            self.root.after(0, lambda: self._notify_error("TWAIN", str(exc)))
        except Exception as exc:
            log.error(f"_scan_twain_worker() error: {exc}")
            log_exception(log, exc, "_scan_twain_worker")
            self.root.after(0, lambda: self._notify_error("Сканирование", str(exc)))
        finally:
            self.root.after(0, self._finish_scan)

    def _startup_select_scanner(self) -> None:
        if self._use_twain and self._twain_driver:
            if self._twain_driver.start():
                self._scanner_label.config(text="TWAIN · готов")
                self._scan_btn.config(state=tk.NORMAL)
                self._set_status("TWAIN драйвер активен")
                return
            else:
                self._use_twain = False

        scanners = find_scanners()
        if not scanners:
            if not messagebox.askyesno(
                "Сканеры не найдены",
                "Подключённые сканеры не обнаружены.\n\n"
                "Подключите сканер и нажмите «Да» для повторного поиска,\n"
                "или «Нет» чтобы открыть станцию без сканера.",
                parent=self.root,
            ):
                self._set_status("Сканер не выбран")
                return
            scanners = find_scanners()

        if len(scanners) == 1:
            self._apply_scanner(scanners[0])
        elif scanners:
            self._select_scanner(scanners, force_dialog=True)
        else:
            self._set_status("Сканер не найден")

    def _apply_scanner(self, scanner: ScannerInfo) -> None:
        self.scanner = scanner
        self._scanner_caps = get_scanner_capabilities(scanner)
        duplex_txt = "дуплекс ✓" if self._scanner_caps.get("duplex") else "дуплекс ✗"
        self._scanner_label.config(text=f"{self.scanner.name[:24]} · {duplex_txt}")
        self._scan_btn.config(state=tk.NORMAL)
        if self._scanner_caps.get("duplex"):
            self._set_status("Сканер готов · А4 · ч/б · дуплекс")
        else:
            self._set_status("Сканер без дуплекса")

    def _select_scanner(
        self,
        scanners: Optional[List[ScannerInfo]] = None,
        *,
        force_dialog: bool = False,
    ) -> None:
        if scanners is None:
            scanners = find_scanners()
        if not scanners:
            messagebox.showwarning("Сканеры", "Подключённые сканеры не найдены.", parent=self.root)
            return

        if not force_dialog and len(scanners) == 1:
            self._apply_scanner(scanners[0])
            return

        dialog = ScannerSelectDialog(self.root, scanners)
        self.root.wait_window(dialog)

        if dialog.selected:
            self._apply_scanner(dialog.selected)
        elif self.scanner is None:
            self._set_status("Сканер не выбран")

    def _finish_scan(self):
        log.info("_finish_scan() - resetting scan state")
        self._scanning = False
        self._scan_btn.config(state=tk.NORMAL)
        self._set_status("Готово")
        log.info("Scan finished, ready for next scan")
            
    def _scan_system_worker(self):
        try:
            image = scan_image_system_dialog()

            self.root.after(
                0,
                lambda: self._on_scan_complete([image]),
            )

        except Exception as exc:
            err_msg = str(exc)
            self.root.after(
                0,
                lambda msg=err_msg: self._notify_error(
                    "Сканирование",
                    msg,
                ),
            )

        finally:
            self.root.after(
                0,
                self._finish_scan,
            )

    def _on_scan_click(self) -> None:
        log.info("=" * 50)
        log.info("_on_scan_click() triggered")
        log.info(f"  _scanning: {self._scanning}")
        log.info(f"  _use_hal: {getattr(self, '_use_hal', False)}")
        log.info(f"  _use_twain: {getattr(self, '_use_twain', False)}")
        log.info(f"  scanner: {self.scanner}")

        if self._scanning:
            log.warning("Already scanning, ignoring click")
            return

        # Check HAL first (continuous ADF support)
        if getattr(self, '_use_hal', False) and self._hw_scanner:
            log.info("Using HAL scanner path")
            self._scanning = True
            self._scan_btn.config(state=tk.DISABLED)
            self._set_status("HAL: запуск сканирования...")
            threading.Thread(target=self._scan_hal_worker, daemon=True).start()
            return

        if self._use_twain and self._twain_driver and self._twain_driver.available:
            log.info("Using TWAIN driver path")
            self._scanning = True
            self._scan_btn.config(state=tk.DISABLED)
            self._set_status("TWAIN: запуск сканирования...")
            threading.Thread(target=self._scan_twain_worker, daemon=True).start()
            return

        if self.scanner is None:
            log.warning("No scanner selected!")
            return

        caps = self._scanner_caps or get_scanner_capabilities(self.scanner)
        log.info(f"Using WIA path, caps: {caps}")

        if not caps.get("duplex"):
            log.info("No duplex, using system dialog")
            self._scanning = True
            self._scan_btn.config(state=tk.DISABLED)
            self._set_status("Ожидание сканирования...")

            threading.Thread(
                target=self._scan_system_worker,
                daemon=True,
            ).start()
            return

        log.info("Using WIA duplex scan")
        self._scanning = True
        self._scan_btn.config(state=tk.DISABLED)
        self._set_status("Сканирование...")
        threading.Thread(target=self._scan_worker, daemon=True).start()

    def _scan_worker(self) -> None:
        log.info("_scan_worker() START (WIA path)")
        try:
            settings = self.config.get("scan_settings", {})
            dpi = int(settings.get("dpi", 300))
            color_mode = settings.get("color_mode", "grayscale")
            duplex = bool(settings.get("duplex", True))
            max_sides = int(settings.get("max_sides_per_sheet", 2))
            page_size = str(settings.get("page_size", "a4"))
            auto_border = bool(settings.get("auto_border", True))

            log.info(f"Scan settings: dpi={dpi}, color={color_mode}, duplex={duplex}")
            log.info(f"  max_sides={max_sides}, page_size={page_size}, auto_border={auto_border}")

            raw_images = scan_sheet_sides(
                self.scanner,
                dpi=dpi,
                color_mode=color_mode,
                duplex=duplex,
                max_sides=max_sides,
                page_size=page_size,
                auto_border=auto_border,
                require_duplex=True,
            )

            log.info(f"scan_sheet_sides returned {len(raw_images)} images")

            side_labels = ["Лицевая", "Оборот"]
            results: List[dict] = []
            skipped = 0

            for part_idx, raw_image in enumerate(raw_images, start=1):
                log.debug(f"Processing image {part_idx}: {raw_image.size}")
                processed = process_scanned_blank(raw_image, self.config, sheet_part=part_idx)
                if not processed.visible or processed.image is None:
                    log.debug(f"Image {part_idx} skipped (not visible or no image)")
                    skipped += 1
                    continue

                recognition = self.recognizer.recognize(
                    processed.image,
                    blank_type=processed.qr_info.type_code if processed.qr_info and processed.qr_info.type_code else "",
                )
                is_special, reasons = evaluate_special_case(recognition, self.config)
                side_label = side_labels[part_idx - 1] if part_idx <= len(side_labels) else f"Сторона {part_idx}"
                results.append({
                    "image": processed.image,
                    "is_special": is_special,
                    "reasons": reasons,
                    "qr_data": processed.qr_data,
                    "qr_info": processed.qr_info,
                    "barcode_id": processed.barcode_id,
                    "id_source": processed.id_source,
                    "is_corrupted": processed.is_corrupted,
                    "has_markers": processed.has_markers,
                    "process_note": processed.reason,
                    "side_label": side_label,
                    "sheet_part": part_idx,
                    "recognition": recognition,
                })

            log.info(f"_scan_worker() done: {len(results)} results, {skipped} skipped")
            self.root.after(
                0,
                lambda r=results, s=skipped, total=len(raw_images): self._on_scan_batch_done(r, s, total),
            )
        except DuplexNotSupportedError as exc:
            log.error(f"DuplexNotSupportedError: {exc}")
            self.root.after(0, lambda e=str(exc): self._on_scan_duplex_error(e))
        except Exception as exc:
            log.error(f"_scan_worker() error: {exc}")
            log_exception(log, exc, "_scan_worker")
            self.root.after(0, lambda e=str(exc): self._on_scan_error(e))

    def _on_scan_duplex_error(self, message: str) -> None:
        self._scanning = False
        self._scan_btn.config(state=tk.NORMAL)
        self._set_status("Нужен дуплекс")
        self._notify_warning("Сканирование", message)

    def _on_scan_batch_done(self, results: List[dict], skipped: int, total_sides: int) -> None:
        self._scanning = False
        self._scan_btn.config(state=tk.NORMAL)

        if not results:
            self._set_status(f"Лист пропущен: {total_sides} сторон без бланка")
            return

        batch_id = str(uuid.uuid4())
        for item in results:
            self._add_blank(scan_batch_id=batch_id, **item)

        self._update_counts()
        self._update_chain_status()
        self._refresh_all_thumbnails()

        msg = f"Добавлено: {len(results)} из {total_sides}"
        if skipped:
            msg += f" (пропущено {skipped})"
        corrupted = corrupted_blanks(self.blanks)
        if corrupted:
            msg += f" · испорченных: {len(corrupted)}"
        self._set_status(msg if corrupted else "Добавлено · Связать")

    def _add_blank(
        self,
        image: Image.Image,
        is_special: bool,
        reasons: List[str],
        scan_batch_id: str = "",
        qr_data: Optional[str] = None,
        qr_info: Optional[QrPayload] = None,
        barcode_id: Optional[int] = None,
        id_source: str = "",
        is_corrupted: bool = False,
        has_markers: bool = False,
        process_note: str = "",
        side_label: str = "",
        sheet_part: int = 1,
        recognition: Optional[RecognitionResult] = None,
    ) -> None:
        index = len(self.blanks) + 1
        blank = ScannedBlank(
            uid=str(uuid.uuid4()),
            image=image,
            index=index,
            scan_batch_id=scan_batch_id,
            is_special=is_special,
            reasons=reasons,
            qr_data=qr_data,
            qr_info=qr_info,
            barcode_id=barcode_id,
            id_source=id_source,
            is_corrupted=is_corrupted,
            has_markers=has_markers,
            side_label=side_label,
            sheet_part=sheet_part,
            recognition=recognition,
        )
        self.blanks.append(blank)

    def _on_scan_error(self, message: str) -> None:
        self._scanning = False
        self._scan_btn.config(state=tk.NORMAL)
        self._set_status("Ошибка")
        self._notify_error("Ошибка сканирования", message)

    def _run_chain_processing(self) -> None:
        if self._processing:
            return
        self._processing = True
        self._link_btn.config(state=tk.DISABLED)
        self._set_status("Построение цепочек...")

        def worker() -> None:
            try:
                result = run_auditorium_processing(
                    self.blanks, self.config, BASE_DIR,
                )
                self.root.after(0, lambda r=result: self._on_chain_done(r))
            except Exception as exc:
                self.root.after(0, lambda e=str(exc): self._on_chain_error(e))

        threading.Thread(target=worker, daemon=True).start()

    def _on_chain_done(self, result: Dict[str, Any]) -> None:
        self._processing = False
        self.auditorium_result = result

        if not result.get("ok"):
            self._set_status("Нет данных OCR")
            self._notify_warning(
                "Связать бланки",
                result.get("message", "Нет бланков с OCR-вероятностями.\nПодключите нейросеть в recognizer.py"),
            )
            self._update_chain_status()
            return

        apply_links_to_blanks(self.blanks, result.get("links", {}))
        self._refresh_all_thumbnails()
        self._update_counts()
        self._update_chain_status()

        if self.diversion_active and not self._diversion_notified:
            self._diversion_notified = True
            detail_text = "\n".join(self._diversion_details[:5])
            if len(self._diversion_details) > 5:
                detail_text += f"\n… и ещё {len(self._diversion_details) - 5}"
            self._notify_warning(
                "Диверсия",
                "Обнаружена попытка подмены бланка.\n"
                "Все бланки подсвечены красным.\n"
                "Удалите лишние и нажмите «Связать бланки».\n\n"
                f"{detail_text}",
            )

        pending = pending_operator_reviews(result)
        if pending and not self._review_notified:
            self._review_notified = True
            self._notify_warning(
                "Спорные связи",
                f"Требуется проверка оператора: {len(pending)} случаев.\n"
                "Нажмите «Спорные» в левой панели.",
            )

        if is_export_ready(result, self.blanks, self.config):
            if not self._ready_notified:
                self._ready_notified = True
                self._notify_info(
                    "Готово",
                    "Все связи построены.\nМожно экспортировать ZIP.",
                )
            self._set_status("Готово")
        elif self.diversion_active:
            self._set_status("Диверсия")
        else:
            self._set_status("Связать" if not result.get("links") else "Проверка")

        if needs_operator_attention(result) and pending:
            self.root.after(300, self._open_manual_review)

    def _on_chain_error(self, message: str) -> None:
        self._processing = False
        self._update_action_buttons()
        self._set_status("Ошибка")
        self._notify_error("Цепочки", message)

    def _open_manual_review(self) -> None:
        if not self.auditorium_result:
            return
        reviews = pending_operator_reviews(self.auditorium_result)
        if not reviews:
            messagebox.showinfo("Проверка", "Спорных связей нет.", parent=self.root)
            return
        ManualReviewDialog(self.root, reviews, self.blanks, self._resolve_operator_input)

    def _open_corrupted_review(self) -> None:
        corrupted = corrupted_blanks(self.blanks)
        if not corrupted:
            messagebox.showinfo("Испорченные", "Испорченных бланков нет.", parent=self.root)
            return
        CorruptedBlankDialog(
            self.root, corrupted, self._resolve_corrupted_blank, self._discard_blank,
        )

    def _discard_blank(self, blank_uid: str) -> None:
        blank = next((b for b in self.blanks if b.uid == blank_uid), None)
        if blank is None:
            return
        self.blanks.remove(blank)
        self._selected_ids.discard(blank_uid)
        self._renumber_blanks()
        self._refresh_all_thumbnails()
        self._update_counts()
        self._diversion_notified = False
        self._review_notified = False
        self._ready_notified = False
        self.auditorium_result = None

        if self.blanks:
            self._update_chain_status()
            self._set_status("Лист удалён")
        else:
            self.diversion_active = False
            self._diversion_details = []
            self._update_chain_status()
            self._set_status("Список пуст")

    def _resolve_corrupted_blank(self, blank_uid: str, number: str) -> None:
        blank = next((b for b in self.blanks if b.uid == blank_uid), None)
        if blank is None:
            return
        blank.operator_blank_id = int(number)
        blank.is_corrupted = False
        blank.id_source = "operator"
        if blank.qr_info:
            info = blank.qr_info
            blank.qr_info = QrPayload(
                valid=info.type_code in {"titul", "blan1", "blan2", "provr"},
                type_code=info.type_code,
                type_label=info.type_label,
                short_label=info.short_label,
                blank_id=int(number),
                work_id=info.work_id,
                side=info.side,
                min_markers=info.min_markers,
                max_markers=info.max_markers,
                corner_only=info.corner_only,
            )
        self._refresh_all_thumbnails()
        self._update_chain_status()
        self._set_status(f"Номер введён: {number[:6]}…")
        if can_link_blanks(self.blanks):
            self._set_status("Можно связать бланки")

    def _resolve_operator_input(self, src: str, dst: str, review_id: Optional[str]) -> None:
        if not self.auditorium_result:
            return
        try:
            result = resolve_operator_input(
                src, dst, self.blanks, self.config, BASE_DIR,
                self.auditorium_result, review_id=review_id,
            )
            self.auditorium_result = result
            apply_links_to_blanks(self.blanks, result.get("links", {}))
            self._refresh_all_thumbnails()
            self._update_chain_status()
            self._set_status(f"Оператор: {src[:6]}… → {dst}")

            if needs_operator_attention(result):
                pending = pending_operator_reviews(result)
                if pending:
                    self.root.after(200, self._open_manual_review)
            elif is_export_ready(result, self.blanks, self.config):
                self._set_status("Готово")
            else:
                self._set_status("Обновлено")
        except Exception as exc:
            self._notify_error("Ошибка", str(exc))

    def _blank_border_style(self, blank: ScannedBlank) -> tuple[str, int]:
        if self.diversion_active:
            return "#dc2626", 4
        if blank.is_corrupted:
            return "#d97706", 3
        if blank.uid in self._selected_ids:
            return "#2563eb", 3
        if blank.is_special:
            return "#dc2626", 2
        return "#e2e8f0", 1

    def _refresh_blank_borders(self) -> None:
        for blank in self.blanks:
            if blank._outer_frame is not None:
                color, thickness = self._blank_border_style(blank)
                blank._outer_frame.config(highlightbackground=color, highlightthickness=thickness)

    def _update_selection_label(self) -> None:
        if self._last_deleted:
            self._undo_btn.config(state=tk.NORMAL)
        else:
            self._undo_btn.config(state=tk.DISABLED)

        n = len(self._selected_ids)
        if n == 0:
            self._selected_label.config(text="—")
            self._delete_btn.config(state=tk.DISABLED)
        elif n == 1:
            blank = next((b for b in self.blanks if b.uid in self._selected_ids), None)
            text = f"#{blank.index}" if blank else "1"
            self._selected_label.config(text=text)
            self._delete_btn.config(state=tk.NORMAL)
        else:
            self._selected_label.config(text=f"Выбрано: {n}")
            self._delete_btn.config(state=tk.NORMAL)

    def _handle_blank_click(self, blank: ScannedBlank, event: tk.Event) -> None:
        ctrl = bool(event.state & 0x0004)  # Control on Windows
        if ctrl:
            if blank.uid in self._selected_ids:
                self._selected_ids.discard(blank.uid)
            else:
                self._selected_ids.add(blank.uid)
        else:
            if blank.uid in self._selected_ids and len(self._selected_ids) == 1:
                self._selected_ids.clear()
            else:
                self._selected_ids = {blank.uid}
        self._update_selection_label()
        self._refresh_blank_borders()

    def _compute_grid_metrics(self) -> Dict[str, int]:
        spacing = int(self.display_cfg.get("thumbnail_spacing", 16))
        base_w = int(float(self.display_cfg.get("base_thumbnail_width", 260)) * self.zoom)
        base_w = max(120, min(base_w, 560))
        canvas_w = max(200, self._canvas.winfo_width())
        cell_w = base_w + spacing * 2 + 12
        cols = max(1, canvas_w // cell_w)
        thumb_w = max(100, (canvas_w - spacing * (cols + 1)) // cols - spacing)
        return {"cols": cols, "thumb_w": thumb_w, "spacing": spacing}

    def _render_blank(self, blank: ScannedBlank, metrics: Dict[str, int]) -> None:
        spacing = metrics["spacing"]
        thumb_w = metrics["thumb_w"]
        img = blank.image.copy()
        ratio = thumb_w / img.width
        w = max(1, int(img.width * ratio))
        h = max(1, int(img.height * ratio))
        img = img.resize((w, h), Image.Resampling.LANCZOS)
        photo = ImageTk.PhotoImage(img)
        blank.photo = photo

        frame = ttk.Frame(self._canvas_frame, padding=6, style="Card.TFrame", width=thumb_w + spacing)
        frame.grid_propagate(True)
        border_color, border_w = self._blank_border_style(blank)
        outer = tk.Frame(
            frame, highlightbackground=border_color,
            highlightthickness=border_w, bg="#ffffff", cursor="hand2",
        )
        outer.pack()
        outer.bind("<Button-1>", lambda e, b=blank: self._handle_blank_click(b, e))

        img_label = tk.Label(outer, image=photo, bg="#ffffff", cursor="hand2")
        img_label.pack(padx=4, pady=4)
        img_label.bind("<Button-1>", lambda e, b=blank: self._handle_blank_click(b, e))

        info = ttk.Frame(frame, style="Card.TFrame")
        info.pack(fill=tk.X, pady=(6, 0))

        type_color = "#1e293b" if blank.blank_number else "#94a3b8"
        ttk.Label(info, text=blank.type_label, font=("Segoe UI", 10, "bold"), foreground=type_color).pack(anchor=tk.W)

        meta_parts = [f"#{blank.index}"]
        if blank.blank_number:
            meta_parts.append(blank.blank_number)
        if blank.is_corrupted:
            meta_parts.append("испорченный QR")
        elif blank.id_source == "barcode":
            meta_parts.append("по штрихкоду")
        elif blank.id_source == "operator":
            meta_parts.append("введён оператором")
        if blank.is_special:
            meta_parts.append("особый случай")
        if self.diversion_active:
            meta_parts.append("диверсия")
        ttk.Label(info, text="  ·  ".join(meta_parts), style="Muted.TLabel", wraplength=thumb_w).pack(anchor=tk.W)

        if blank.link_next:
            ttk.Label(
                info, text=f"→ {blank.link_next}",
                font=("Consolas", 9), foreground="#2563eb",
            ).pack(anchor=tk.W)

        blank._ui_frame = frame
        blank._outer_frame = outer

    def _renumber_blanks(self) -> None:
        for i, blank in enumerate(self.blanks, start=1):
            blank.index = i

    def _undo_delete(self):
        if not self._last_deleted:
            return

        self.blanks.extend(self._last_deleted)
        self._last_deleted = []

        self.blanks.sort(key=lambda b: b.index)
        self._renumber_blanks()

        self.auditorium_result = None
        self._diversion_notified = False
        self._review_notified = False
        self._ready_notified = False

        self._refresh_all_thumbnails()
        self._update_counts()
        self._update_chain_status()
        self._update_selection_label()

        self._set_status("Удаление отменено")

    def _select_all(self):
        self._selected_ids = {b.uid for b in self.blanks}
        self._update_selection_label()
        self._refresh_blank_borders()

    def _delete_selected(self) -> None:
        if not self._selected_ids:
            return
        selected = [b for b in self.blanks if b.uid in self._selected_ids]
        self._last_deleted = selected.copy()
        if not selected:
            return
        if len(selected) == 1:
            prompt = f"Удалить «{selected[0].type_label}» (#{selected[0].index})?"
        else:
            prompt = f"Удалить {len(selected)} бланков?"
        if not messagebox.askyesno("Удалить", prompt, parent=self.root):
            return
        for blank in selected:
            if blank in self.blanks:
                self.blanks.remove(blank)
        self._selected_ids.clear()
        self._update_selection_label()
        self._renumber_blanks()
        self._refresh_all_thumbnails()
        self._update_counts()
        self._diversion_notified = False
        self._review_notified = False
        self._ready_notified = False
        self.auditorium_result = None
        if self.blanks:
            self._set_status("Пересчёт")
            self._run_chain_processing()
        else:
            self.diversion_active = False
            self._diversion_details = []
            self._update_chain_status()
            self._set_status("Список пуст")

    def _export_zip(self) -> None:
        if not self.blanks:
            messagebox.showinfo("Экспорт", "Нет бланков для экспорта.", parent=self.root)
            return

        if not is_export_ready(self.auditorium_result, self.blanks, self.config):
            self._notify_warning(
                "Экспорт",
                "Экспорт недоступен.\n"
                "Свяжите бланки, решите спорные случаи и устраните диверсию.",
            )
            return

        work_id = session_work_id(self.blanks)
        if not work_id:
            messagebox.showwarning(
                "Экспорт", "work_id не найден в QR бланков.", parent=self.root,
            )
            return

        path = filedialog.asksaveasfilename(
            parent=self.root,
            title="Сохранить ZIP",
            defaultextension=".zip",
            initialfile=f"{work_id}.zip",
            filetypes=[("ZIP", "*.zip"), ("Все файлы", "*.*")],
        )
        if not path:
            return

        try:
            result = export_work_zip(
                self.blanks, self.auditorium_result, Path(path), self.config,
            )
            if not result.get("ok"):
                messagebox.showwarning("Экспорт", result.get("message", "Ошибка"), parent=self.root)
                return
            self._set_status(f"ZIP: {Path(path).name} ({result['saved']} файлов)")
            messagebox.showinfo(
                "Экспорт",
                f"Сохранено {result['saved']} изображений.\n\n{path}",
                parent=self.root,
            )
            self._reset_session(confirm=False, status="Экспорт завершён · сброс")
        except Exception as exc:
            messagebox.showerror("Ошибка экспорта", str(exc), parent=self.root)

    def _reset_session(self, *, confirm: bool = True, status: str = "Сброс") -> None:
        has_data = bool(self.blanks) or self.auditorium_result is not None
        if confirm:
            if not has_data:
                return
            if not messagebox.askyesno(
                "Сбросить",
                "Сбросить всю сессию?\n\n"
                "Будут удалены все бланки, связи, решения оператора\n"
                "и спорные случаи — как при новом запуске.",
                parent=self.root,
            ):
                return

        self.blanks.clear()
        self.auditorium_result = None
        self.diversion_active = False
        self._diversion_details = []
        self._diversion_notified = False
        self._review_notified = False
        self._ready_notified = False
        self._selected_ids.clear()
        self._scanning = False
        self._processing = False

        reset_operator_session(self.config, BASE_DIR)

        for child in self._canvas_frame.winfo_children():
            child.destroy()

        self._update_selection_label()
        self._update_counts()
        self._chain_status_label.config(text="Нет уведомлений", foreground="#64748b")
        self._review_btn.config(text="Спорные (0)", state=tk.DISABLED)
        self._corrupted_btn.config(text="Испорченные (0)", state=tk.DISABLED)
        self._links_count_label.config(text="0")
        self._link_btn.config(state=tk.DISABLED)
        self._export_btn.config(state=tk.DISABLED)
        if self.scanner is not None:
            self._scan_btn.config(state=tk.NORMAL)
        self._set_status(status)

    def _refresh_all_thumbnails(self) -> None:
        for child in self._canvas_frame.winfo_children():
            child.destroy()

        if not self.blanks:
            self._on_frame_configure()
            return

        self._canvas.update_idletasks()
        metrics = self._compute_grid_metrics()
        cols = metrics["cols"]
        spacing = metrics["spacing"]

        for col in range(cols):
            self._canvas_frame.columnconfigure(col, weight=1, uniform="blank_cols")

        for idx, blank in enumerate(self.blanks):
            self._render_blank(blank, metrics)
            row, col = divmod(idx, cols)
            if blank._ui_frame is not None:
                blank._ui_frame.grid(
                    row=row, column=col,
                    padx=spacing // 2, pady=spacing // 2,
                    sticky=tk.N,
                )
    def _init_scanners(self) -> None:
        log.info("=" * 50)
        log.info("_init_scanners() - Initializing scanner subsystem")
        log.info(f"  HAL_AVAILABLE: {HAL_AVAILABLE}")

        if HAL_AVAILABLE:
            log.info("Attempting HAL scanner initialization...")
            try:
                self._hw_scanner = HardwareScanner()
                self._use_hal = True
                log.info("SUCCESS: Using HAL scanner (continuous ADF support)")
                return
            except Exception as e:
                log.warning(f"HAL init failed: {e}")
                log_exception(log, e, "HAL init")

        twain_cfg = self.config.get("twain", {})
        log.info(f"TWAIN config: {twain_cfg}")
        if twain_cfg.get("enabled", True):
            log.info("Attempting TWAIN driver initialization...")
            self._twain_driver = get_twain_driver()
            if self._twain_driver and self._twain_driver.available:
                self._use_twain = True
                log.info("SUCCESS: Using TWAIN driver")
                return
            else:
                log.warning("TWAIN driver not available")

        log.warning("No HAL or TWAIN available - will use WIA fallback")
        log.info("=" * 50)

    def _scan_hal_worker(self) -> None:
        log.info("_scan_hal_worker() START")
        try:
            images = self._hw_scanner.scan_batch(
                on_page=lambda img, idx: self.root.after(
                    0, self._update_progress, f"Page {idx + 1}"
                )
            )
            log.info(f"_scan_hal_worker() got {len(images)} images")
            self.root.after(0, self._on_scan_complete, images)
        except Exception as e:
            log.error(f"_scan_hal_worker() error: {e}")
            log_exception(log, e, "_scan_hal_worker")
            self.root.after(0, self._on_scan_error, str(e))

    def _zoom_in(self) -> None:
        step = float(self.display_cfg.get("zoom_step", 0.1))
        max_z = float(self.display_cfg.get("max_zoom", 3.0))
        self.zoom = min(max_z, round(self.zoom + step, 2))
        self._zoom_label.config(text=f"{int(self.zoom * 100)}%")
        self._refresh_all_thumbnails()

    def _zoom_out(self) -> None:
        step = float(self.display_cfg.get("zoom_step", 0.1))
        min_z = float(self.display_cfg.get("min_zoom", 0.3))
        self.zoom = max(min_z, round(self.zoom - step, 2))
        self._zoom_label.config(text=f"{int(self.zoom * 100)}%")
        self._refresh_all_thumbnails()

    def _cleanup(self) -> None:
        if self._twain_driver:
            self._twain_driver.stop()

    def run(self) -> None:
        try:
            self.root.mainloop()
        finally:
            self._cleanup()


def main() -> None:
    if sys.platform != "win32":
        print("Станция сканирования работает только на Windows 10/11.")
        sys.exit(1)

    # Print log file location for debugging
    print(f"[DEBUG] Log file: {get_log_file()}")
    log.info("=" * 60)
    log.info("APPLICATION STARTING")
    log.info(f"Python: {sys.version}")
    log.info(f"Platform: {sys.platform}")
    log.info(f"HAL_AVAILABLE: {HAL_AVAILABLE}")
    log.info("=" * 60)
    app = ScanStationApp()
    app.run()


if __name__ == "__main__":
    main()
