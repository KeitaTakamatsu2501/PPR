"""PPR の編集画面。

本文は `【...】` 見出しで区画へ分け、区画単位で編集する。実データではミラの本文が
62区画あり、単一のテキストエリアでは扱えない（docs/PHASE_0_FINDINGS.md §2）。

Tk Text からの取得は `end-1c` を使い、Tkが付ける末尾改行だけを除く。
内容末尾の空白・改行は `.strip()` で消さない。

再描画中の事故防止について：
ウィジェットの中身を差し替えると Tk は `<<Modified>>` や `<<TreeviewSelect>>` を
発火する。これを素朴に真偽値1個で抑止すると、入れ子になった描画処理が外側の抑止を
解除してしまい、直前に表示していた人格の本文が新しい人格へ書き込まれる。
そのため抑止は深さカウンタで管理し、さらに書き込み側でも `EditTarget` による
束縛照合を行う。どちらか一方が欠けても事故は起きない。
"""

from __future__ import annotations

import contextlib
import tkinter as tk
from tkinter import messagebox, ttk

from ..config import AppConfig
from ..domain import HUMANITY_MEMBERSHIPS, PERCEIVED_HUMANITY_OVERRIDES, Library
from ..storage import LibraryLock, LibraryLockedError, LibraryStore
from .state import BASIC_FIELDS, BODY_FIELDS, BODY_FIELD_LABELS, EditorState, EditTarget

PERSON_FIELDS: tuple[tuple[str, str], ...] = (
    ("name", "名前"),
    ("target_character_id", "相手のID"),
    ("direct_address_override", "呼称の例外"),
    ("perceived_humanity_override", "人類認識の例外"),
)

STATUS_DEBOUNCE_MS = 200


def text_value(widget: tk.Text) -> str:
    """Tkが付ける末尾の改行1つだけを除く。作者が入れた空白・改行は残す。"""
    return widget.get("1.0", "end-1c")


class PprApp(ttk.Frame):
    def __init__(self, master: tk.Tk, config: AppConfig, library: Library, store: LibraryStore):
        super().__init__(master, padding=8)
        self.config_obj = config
        self.store = store
        self.library = library
        self.state: EditorState | None = None

        self._sync_depth = 0
        self._status_job: str | None = None
        self._body_target: EditTarget | None = None
        self._topic_target: EditTarget | None = None
        self._person_target: EditTarget | None = None

        self.pack(fill="both", expand=True)

        self.locale_var = tk.StringVar(value="ja-JP")
        self.status_var = tk.StringVar(value="")
        self.title_var = tk.StringVar(value="人格を選んでください")
        self.body_field_var = tk.StringVar(value=BODY_FIELDS[0][0])
        self.basic_vars = {name: tk.StringVar() for name, _ in BASIC_FIELDS}
        self.person_vars = {name: tk.StringVar() for name, _ in PERSON_FIELDS}

        self._build()
        self._reload_persona_list()

    # ---- 再描画の抑止 ---------------------------------------------------

    @contextlib.contextmanager
    def _syncing(self):
        """描画由来のイベントを抑止する。入れ子にしても外側の抑止を壊さない。"""
        self._sync_depth += 1
        try:
            yield
        finally:
            self._sync_depth -= 1

    @property
    def _syncing_now(self) -> bool:
        return self._sync_depth > 0

    def _set_text(self, widget: tk.Text, value: str) -> None:
        with self._syncing():
            widget.delete("1.0", "end")
            widget.insert("1.0", value)
            widget.edit_reset()
            widget.edit_modified(False)

    # ---- 構築 ---------------------------------------------------------

    def _build(self) -> None:
        header = ttk.Frame(self)
        header.pack(fill="x")
        ttk.Label(header, text="locale").pack(side="left")
        self.locale_box = ttk.Combobox(header, textvariable=self.locale_var, width=10, state="readonly")
        self.locale_box.pack(side="left", padx=(4, 12))
        self.locale_box.bind("<<ComboboxSelected>>", self._on_locale_changed)
        ttk.Label(header, textvariable=self.title_var, font=("", 13, "bold")).pack(side="left")

        panes = ttk.PanedWindow(self, orient="horizontal")
        panes.pack(fill="both", expand=True, pady=8)

        left = ttk.Frame(panes)
        self.persona_list = ttk.Treeview(left, columns=("chars",), show="tree headings", height=20)
        self.persona_list.heading("#0", text="人格")
        self.persona_list.heading("chars", text="本文字数")
        self.persona_list.column("#0", width=160)
        self.persona_list.column("chars", width=80, anchor="e")
        self.persona_list.pack(fill="both", expand=True)
        self.persona_list.bind("<<TreeviewSelect>>", self._on_persona_selected)
        panes.add(left, weight=1)

        right = ttk.Frame(panes)
        self.tabs = ttk.Notebook(right)
        self.tabs.pack(fill="both", expand=True)
        self.tabs.add(self._build_basic_tab(), text="基本")
        self.tabs.add(self._build_body_tab(), text="本文")
        self.tabs.add(self._build_topic_tab(), text="価値観")
        self.tabs.add(self._build_person_tab(), text="人物関係")
        panes.add(right, weight=4)

        footer = ttk.Frame(self)
        footer.pack(fill="x")
        ttk.Label(footer, textvariable=self.status_var).pack(side="left")
        self.save_button = ttk.Button(footer, text="保存", command=self.save)
        self.save_button.pack(side="right")

    def _build_basic_tab(self) -> ttk.Frame:
        frame = ttk.Frame(self.tabs, padding=12)
        for row, (name, label) in enumerate(BASIC_FIELDS):
            ttk.Label(frame, text=label).grid(row=row, column=0, sticky="w", pady=4)
            if name == "humanity_membership":
                widget = ttk.Combobox(
                    frame, textvariable=self.basic_vars[name], width=40,
                    values=list(HUMANITY_MEMBERSHIPS), state="readonly",
                )
            else:
                widget = ttk.Entry(frame, textvariable=self.basic_vars[name], width=42)
            widget.grid(row=row, column=1, sticky="we", pady=4, padx=(8, 0))
            self.basic_vars[name].trace_add("write", self._on_basic_changed)
        frame.columnconfigure(1, weight=1)
        self.revision_label = ttk.Label(frame, text="", foreground="#666", justify="left")
        self.revision_label.grid(row=len(BASIC_FIELDS), column=0, columnspan=2, sticky="w", pady=(16, 0))
        return frame

    def _build_body_tab(self) -> ttk.Frame:
        frame = ttk.Frame(self.tabs, padding=8)
        bar = ttk.Frame(frame)
        bar.pack(fill="x")
        for name, label in BODY_FIELDS:
            ttk.Radiobutton(
                bar, text=label, value=name, variable=self.body_field_var,
                command=self._on_body_field_changed,
            ).pack(side="left", padx=(0, 12))

        panes = ttk.PanedWindow(frame, orient="horizontal")
        panes.pack(fill="both", expand=True, pady=(8, 0))
        left = ttk.Frame(panes)
        self.section_list = ttk.Treeview(left, show="tree", height=18)
        self.section_list.pack(fill="both", expand=True)
        self.section_list.bind("<<TreeviewSelect>>", self._on_section_selected)
        panes.add(left, weight=2)

        right = ttk.Frame(panes)
        self.section_hint = ttk.Label(right, text="", foreground="#666")
        self.section_hint.pack(anchor="w")
        holder = ttk.Frame(right)
        holder.pack(fill="both", expand=True)
        self.body_text = tk.Text(holder, wrap="word", undo=True, height=20, font=("", 12))
        scroll = ttk.Scrollbar(holder, command=self.body_text.yview)
        self.body_text.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.body_text.pack(side="left", fill="both", expand=True)
        self.body_text.bind("<<Modified>>", self._on_body_modified)
        panes.add(right, weight=3)
        return frame

    def _build_topic_tab(self) -> ttk.Frame:
        frame = ttk.Frame(self.tabs, padding=8)
        panes = ttk.PanedWindow(frame, orient="horizontal")
        panes.pack(fill="both", expand=True)
        left = ttk.Frame(panes)
        self.topic_tree = ttk.Treeview(left, show="tree", height=20)
        self.topic_tree.pack(fill="both", expand=True)
        self.topic_tree.bind("<<TreeviewSelect>>", self._on_topic_selected)
        panes.add(left, weight=2)

        right = ttk.Frame(panes)
        self.topic_label = ttk.Label(right, text="", foreground="#666")
        self.topic_label.pack(anchor="w")
        self.topic_text = tk.Text(right, wrap="word", undo=True, height=18, font=("", 12))
        self.topic_text.pack(fill="both", expand=True)
        self.topic_text.bind("<<Modified>>", self._on_topic_modified)
        panes.add(right, weight=3)
        return frame

    def _build_person_tab(self) -> ttk.Frame:
        frame = ttk.Frame(self.tabs, padding=8)
        self.person_tree = ttk.Treeview(
            frame, columns=("target", "address"), show="tree headings", height=8
        )
        self.person_tree.heading("#0", text="名前")
        self.person_tree.heading("target", text="相手のID")
        self.person_tree.heading("address", text="呼称の例外")
        self.person_tree.column("#0", width=140)
        self.person_tree.column("target", width=200)
        self.person_tree.column("address", width=140)
        self.person_tree.pack(fill="x")
        self.person_tree.bind("<<TreeviewSelect>>", self._on_person_selected)

        fields = ttk.Frame(frame)
        fields.pack(fill="x", pady=8)
        for column, (name, label) in enumerate(PERSON_FIELDS):
            ttk.Label(fields, text=label).grid(row=0, column=column * 2, sticky="w", padx=(0, 4))
            if name == "perceived_humanity_override":
                widget = ttk.Combobox(
                    fields, textvariable=self.person_vars[name], width=14,
                    values=list(PERCEIVED_HUMANITY_OVERRIDES), state="readonly",
                )
            else:
                widget = ttk.Entry(fields, textvariable=self.person_vars[name], width=18)
            widget.grid(row=0, column=column * 2 + 1, sticky="we", padx=(0, 12))
            self.person_vars[name].trace_add("write", self._on_person_field_changed)

        self.person_note = ttk.Label(frame, text="", foreground="#a33")
        self.person_note.pack(anchor="w")
        self.person_text = tk.Text(frame, wrap="word", undo=True, height=10, font=("", 12))
        self.person_text.pack(fill="both", expand=True)
        self.person_text.bind("<<Modified>>", self._on_person_modified)
        return frame

    # ---- 一覧 ---------------------------------------------------------

    def _available_locales(self) -> list[str]:
        locales: list[str] = []
        for record in self.library.personas:
            for locale in record.locales:
                if locale not in locales:
                    locales.append(locale)
        return locales or ["ja-JP"]

    def _reload_persona_list(self, *, select: str | None = None) -> None:
        locales = self._available_locales()
        self.locale_box["values"] = locales
        if self.locale_var.get() not in locales:
            self.locale_var.set(locales[0])
        locale = self.locale_var.get()

        with self._syncing():
            self.persona_list.delete(*self.persona_list.get_children())
            for record in self.library.personas:
                document = record.document(locale)
                if document is None:
                    continue
                self.persona_list.insert(
                    "", "end", iid=record.persona_id,
                    text=document.name or record.persona_id,
                    values=(f"{document.body_characters:,}",),
                )
        children = self.persona_list.get_children()
        if not children:
            self.title_var.set("人格がありません")
            self.status_var.set("取り込み:  python -m ppr import-jts --jts-root <JTSのパス>")
            return
        wanted = select if select in children else children[0]
        with self._syncing():
            self.persona_list.selection_set(wanted)
        self._open_persona(wanted)

    def _on_locale_changed(self, _event: object = None) -> None:
        current = self.state
        if current is not None and current.locale != self.locale_var.get() and current.dirty:
            if not messagebox.askokcancel(
                "未保存の変更",
                f"「{current.document.name}」に保存していない変更があります。破棄してlocaleを切り替えますか。",
            ):
                with self._syncing():
                    self.locale_var.set(current.locale)
                return
        keep = current.persona_id if current is not None else None
        self._reload_persona_list(select=keep)

    # ---- 選択 ---------------------------------------------------------

    def _on_persona_selected(self, _event: object = None) -> None:
        if self._syncing_now:
            return
        selection = self.persona_list.selection()
        if not selection:
            return
        persona_id = selection[0]
        current = self.state
        if current is not None and current.persona_id == persona_id:
            return
        if current is not None and current.dirty:
            if not messagebox.askokcancel(
                "未保存の変更",
                f"「{current.document.name}」に保存していない変更があります。破棄して切り替えますか。",
            ):
                with self._syncing():
                    self.persona_list.selection_set(current.persona_id)
                return
        self._open_persona(persona_id)

    def _open_persona(self, persona_id: str) -> None:
        self.state = EditorState.open(self.library, persona_id, self.locale_var.get())
        self._body_target = None
        self._topic_target = None
        self._person_target = None
        self._refresh_all()

    def _refresh_all(self) -> None:
        state = self.state
        if state is None:
            return
        with self._syncing():
            document = state.document
            self.title_var.set(f"{document.name or document.persona_id}  ({document.persona_id})")
            for name, _ in BASIC_FIELDS:
                self.basic_vars[name].set(getattr(document, name))
            self._refresh_sections()
            self._refresh_topics()
            self._refresh_persons()
        self._refresh_status()

    def _refresh_status(self) -> None:
        state = self.state
        if state is None:
            return
        mark = "● 未保存の変更あり" if state.dirty else "保存済み"
        self.status_var.set(f"{mark}   revision {state.current_revision[:12]}…")
        self.revision_label.configure(
            text=f"persona_id {state.persona_id}    locale {state.locale}\n"
                 f"revision {state.current_revision}"
        )

    def _schedule_status(self) -> None:
        """打鍵ごとに全文ハッシュを取り直さないための遅延更新。"""
        if self._status_job is not None:
            self.after_cancel(self._status_job)
        self._status_job = self.after(STATUS_DEBOUNCE_MS, self._run_status_job)

    def _run_status_job(self) -> None:
        self._status_job = None
        self._refresh_status()

    # ---- 本文 ---------------------------------------------------------

    def _refresh_sections(self, *, select_index: int = 0) -> None:
        state = self.state
        if state is None:
            return
        field = self.body_field_var.get()
        with self._syncing():
            self.section_list.delete(*self.section_list.get_children())
            sections = state.sections(field)
            for index, section in enumerate(sections):
                label = section.heading or "（前書き）"
                preview = section.preview
                self.section_list.insert(
                    "", "end", iid=str(index),
                    text=f"{label}　—　{preview}" if preview else label,
                )
        children = self.section_list.get_children()
        if not children:
            self._body_target = None
            self._set_text(self.body_text, "")
            return
        wanted = str(select_index) if str(select_index) in children else children[0]
        with self._syncing():
            self.section_list.selection_set(wanted)
        self._show_section(int(wanted))

    def _show_section(self, index: int) -> None:
        state = self.state
        if state is None:
            return
        field = self.body_field_var.get()
        sections = state.sections(field)
        if not 0 <= index < len(sections):
            return
        self._body_target = state.target_for_section(field, index)
        self._set_text(self.body_text, sections[index].text)
        label = BODY_FIELD_LABELS[field]
        self.section_hint.configure(
            text=f"{label} / 区画 {index + 1}（全{len(sections)}区画）"
                 "　見出し行を含めて編集します。ほかの区画は変更されません。"
        )

    def _on_body_field_changed(self) -> None:
        # 本文の種類が切り替わる前の内容を、切り替え前の束縛で書き戻す。
        self._flush_body()
        self._body_target = None
        self._refresh_sections()

    def _on_section_selected(self, _event: object = None) -> None:
        if self._syncing_now:
            return
        self._flush_body()
        selection = self.section_list.selection()
        if not selection:
            return
        self._show_section(int(selection[0]))

    def _flush_body(self) -> None:
        state = self.state
        if state is None or self._body_target is None:
            return
        if state.apply_section(self._body_target, text_value(self.body_text)):
            self._schedule_status()

    def _on_body_modified(self, _event: object = None) -> None:
        if not self.body_text.edit_modified():
            return
        self.body_text.edit_modified(False)
        if self._syncing_now:
            return
        self._flush_body()

    # ---- 価値観 -------------------------------------------------------

    def _refresh_topics(self) -> None:
        state = self.state
        if state is None:
            return
        with self._syncing():
            self.topic_tree.delete(*self.topic_tree.get_children())
            for group, rows in state.grouped_topics():
                parent = self.topic_tree.insert(
                    "", "end", iid=f"g:{group}", text=f"{group}（{len(rows)}）", open=False
                )
                for row in rows:
                    leaf = row.category.split("/", 1)[1] if "/" in row.category else row.category
                    marker = "" if row.stance.strip() else "　（未記入）"
                    self.topic_tree.insert(parent, "end", iid=row.entry_id, text=f"{leaf}{marker}")
        self._topic_target = None
        self._set_text(self.topic_text, "")
        self.topic_label.configure(text="")

    def _on_topic_selected(self, _event: object = None) -> None:
        if self._syncing_now:
            return
        self._flush_topic()
        state = self.state
        selection = self.topic_tree.selection()
        if state is None or not selection or selection[0].startswith("g:"):
            return
        entry_id = selection[0]
        row = next((r for r in state.document.topic_stances if r.entry_id == entry_id), None)
        if row is None:
            return
        self._topic_target = state.target_for_topic(entry_id)
        self._set_text(self.topic_text, row.stance)
        note = "　この行は試演で常に参照されます。" if row.is_value_boundary else ""
        self.topic_label.configure(text=f"{row.category}{note}")

    def _flush_topic(self) -> None:
        state = self.state
        if state is None or self._topic_target is None:
            return
        if state.apply_topic(self._topic_target, text_value(self.topic_text)):
            self._schedule_status()

    def _on_topic_modified(self, _event: object = None) -> None:
        if not self.topic_text.edit_modified():
            return
        self.topic_text.edit_modified(False)
        if self._syncing_now:
            return
        self._flush_topic()

    # ---- 人物関係 -----------------------------------------------------

    def _refresh_persons(self) -> None:
        state = self.state
        if state is None:
            return
        unresolved = {row.entry_id for row in state.unresolved_person_targets()}
        with self._syncing():
            self.person_tree.delete(*self.person_tree.get_children())
            for row in state.document.person_stances:
                mark = "　⚠" if row.entry_id in unresolved else ""
                self.person_tree.insert(
                    "", "end", iid=row.entry_id,
                    text=f"{row.name}{mark}",
                    values=(row.target_character_id, row.direct_address_override),
                )
            self._person_target = None
            for name, _ in PERSON_FIELDS:
                self.person_vars[name].set("")
        self._set_text(self.person_text, "")
        count = len(unresolved)
        self.person_note.configure(
            text=(f"PPR内に存在しない相手を指す関係が {count} 件あります。"
                  "保持されますが、試演の配役には出ません。" if count else "")
        )

    def _on_person_selected(self, _event: object = None) -> None:
        if self._syncing_now:
            return
        self._flush_person()
        state = self.state
        selection = self.person_tree.selection()
        if state is None or not selection:
            return
        entry_id = selection[0]
        row = next((r for r in state.document.person_stances if r.entry_id == entry_id), None)
        if row is None:
            return
        self._person_target = state.target_for_person(entry_id)
        with self._syncing():
            for name, _ in PERSON_FIELDS:
                self.person_vars[name].set(getattr(row, name))
        self._set_text(self.person_text, row.stance)

    def _flush_person(self) -> None:
        state = self.state
        if state is None or self._person_target is None:
            return
        changes = {name: self.person_vars[name].get() for name, _ in PERSON_FIELDS}
        changes["stance"] = text_value(self.person_text)
        if state.apply_person(self._person_target, changes):
            self._schedule_status()
            entry_id = self._person_target.entry_id
            row = next((r for r in state.document.person_stances if r.entry_id == entry_id), None)
            if row is not None and self.person_tree.exists(entry_id):
                with self._syncing():
                    self.person_tree.item(
                        entry_id, text=row.name,
                        values=(row.target_character_id, row.direct_address_override),
                    )

    def _on_person_field_changed(self, *_args: object) -> None:
        if self._syncing_now:
            return
        self._flush_person()

    def _on_person_modified(self, _event: object = None) -> None:
        if not self.person_text.edit_modified():
            return
        self.person_text.edit_modified(False)
        if self._syncing_now:
            return
        self._flush_person()

    # ---- 基本 ---------------------------------------------------------

    def _on_basic_changed(self, *_args: object) -> None:
        state = self.state
        if state is None or self._syncing_now:
            return
        changed = False
        for name, _ in BASIC_FIELDS:
            value = self.basic_vars[name].get()
            if getattr(state.document, name) != value:
                state.set_basic(name, value)
                changed = True
        if changed:
            self.title_var.set(f"{state.document.name or state.persona_id}  ({state.persona_id})")
            self._schedule_status()

    # ---- 保存 ---------------------------------------------------------

    def save(self) -> None:
        state = self.state
        if state is None:
            return
        self._flush_body()
        self._flush_topic()
        self._flush_person()
        issues = state.document.validation_issues()
        if issues:
            messagebox.showerror("保存できません", "\n".join(issues))
            return
        try:
            self.library = state.commit()
            self.store.save(self.library)
        except OSError as exc:
            messagebox.showerror("保存に失敗しました", f"{exc}\n未保存の状態を保持しています。")
            return
        # 一覧の本文字数を更新する。開いている人格は選び直さない。
        document = state.document
        if self.persona_list.exists(state.persona_id):
            with self._syncing():
                self.persona_list.item(
                    state.persona_id,
                    text=document.name or state.persona_id,
                    values=(f"{document.body_characters:,}",),
                )
        self._refresh_status()

    def on_close(self) -> None:
        if self.state is not None:
            self._flush_body()
            self._flush_topic()
            self._flush_person()
            if self.state.dirty and not messagebox.askokcancel(
                "未保存の変更", "保存していない変更があります。終了しますか。"
            ):
                return
        self.master.destroy()


def run_app(config: AppConfig) -> int:
    config.ensure_directories()
    store = LibraryStore(config)
    lock = LibraryLock(config)
    try:
        lock.acquire()
    except LibraryLockedError as exc:
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror("起動できません", str(exc))
        root.destroy()
        return 2
    try:
        library = store.load()
        root = tk.Tk()
        root.title("PPR — 人格ビルダー")
        root.geometry("1180x760")
        app = PprApp(root, config, library, store)
        root.protocol("WM_DELETE_WINDOW", app.on_close)
        root.mainloop()
    finally:
        lock.release()
    return 0
