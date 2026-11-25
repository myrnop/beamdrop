from pathlib import Path
import secrets
import datetime
import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw, Gio, Gdk, GLib

from .qr_utils import get_local_lan_ip, make_qr_picture
from .server import BeamHttpServer, APK_PATH


APP_CSS = """
@keyframes pulse-glow {
    0%   { box-shadow: 0 0 0 0 alpha(#3584e4, 0.25); }
    50%  { box-shadow: 0 0 0 6px alpha(#3584e4, 0.0); }
    100% { box-shadow: 0 0 0 0 alpha(#3584e4, 0.0); }
}

@keyframes pulse-glow-green {
    0%   { box-shadow: 0 0 0 0 alpha(#2ec27e, 0.30); }
    50%  { box-shadow: 0 0 0 6px alpha(#2ec27e, 0.0); }
    100% { box-shadow: 0 0 0 0 alpha(#2ec27e, 0.0); }
}

@keyframes float-icon {
    0%   { transform: translateY(0px); }
    50%  { transform: translateY(-4px); }
    100% { transform: translateY(0px); }
}

button, .qr-pass, .network-badge, .drop-zone, row {
    transition: all 240ms cubic-bezier(0.22, 1, 0.36, 1);
}

button.pill:hover {
    transform: translateY(-1.5px);
    box-shadow: 0 4px 12px rgba(0, 0, 0, 0.18);
}

button.pill:active {
    transform: translateY(0px) scale(0.98);
}

.qr-pass {
    background-color: #ffffff;
    border-radius: 24px;
    padding: 18px;
    border: 3px solid alpha(#3584e4, 0.38);
    box-shadow: 0 10px 28px rgba(0, 0, 0, 0.18);
}
.qr-pass:hover {
    transform: translateY(-2px);
    box-shadow: 0 14px 32px rgba(0, 0, 0, 0.24);
}
.qr-pass.mode-receive {
    border-color: alpha(#2ec27e, 0.48);
}
.qr-pass.mode-text {
    border-color: alpha(#9141ac, 0.48);
}

.network-badge {
    background-color: alpha(#3584e4, 0.15);
    color: #3584e4;
    border: 1px solid alpha(#3584e4, 0.30);
    border-radius: 999px;
    padding: 6px 16px;
    font-weight: 700;
    font-size: 0.85em;
    font-variant-numeric: tabular-nums;
    animation: pulse-glow 2.8s infinite ease-out;
}
.network-badge.mode-receive, .network-badge.device-connected {
    background-color: alpha(#2ec27e, 0.16);
    color: #26a269;
    border-color: alpha(#2ec27e, 0.38);
    animation: pulse-glow-green 2.8s infinite ease-out;
}
.network-badge.mode-text {
    background-color: alpha(#9141ac, 0.16);
    color: #9141ac;
    border-color: alpha(#9141ac, 0.35);
    animation: none;
}

.drop-zone {
    background: linear-gradient(180deg, alpha(#3584e4, 0.11) 0%, alpha(#62a0ea, 0.03) 100%);
    border: 2px dashed alpha(#3584e4, 0.38);
    border-radius: 26px;
    padding: 14px;
}
.drop-zone:hover {
    border-color: alpha(#3584e4, 0.65);
    background: linear-gradient(180deg, alpha(#3584e4, 0.15) 0%, alpha(#62a0ea, 0.05) 100%);
}
.drop-zone.drag-hover {
    background: linear-gradient(180deg, alpha(#3584e4, 0.25) 0%, alpha(#62a0ea, 0.12) 100%);
    border-color: #3584e4;
    border-style: solid;
    transform: scale(1.018);
    box-shadow: 0 12px 30px alpha(#3584e4, 0.22);
}

.drop-zone statuspage image {
    animation: float-icon 3.2s ease-in-out infinite;
}

.btn-receive {
    background-color: #2ec27e;
    color: #ffffff;
}
.btn-receive:hover {
    background-color: #26a269;
}
.btn-text {
    background-color: #9141ac;
    color: #ffffff;
}
.btn-text:hover {
    background-color: #813d9c;
}

.icon-sent {
    color: #3584e4;
}
.icon-received {
    color: #2ec27e;
}
.icon-text {
    color: #9141ac;
}

.text-card {
    padding: 4px;
}

.transfer-progress progress, .transfer-progress trough {
    min-height: 6px;
    border-radius: 999px;
}

.tabular-stats {
    font-variant-numeric: tabular-nums;
}
"""


def _format_size(num_bytes: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if num_bytes < 1024.0:
            return f"{num_bytes:.0f} {unit}" if unit == "B" else f"{num_bytes:.1f} {unit}"
        num_bytes /= 1024.0
    return f"{num_bytes:.1f} TB"


def _calculate_path_bytes(path: Path) -> int:
    if path.is_file():
        return path.stat().st_size
    if path.is_dir():
        return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())
    return 0


class BeamDropWindow(Adw.ApplicationWindow):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.set_title("BeamDrop")
        self.set_default_size(530, 760)
        self.set_size_request(360, 560)

        self._load_css()

        self.lan_ip = get_local_lan_ip()
        self.server = BeamHttpServer(on_event_cb=self._on_transfer_event)
        self.server.start()
        self.connected_device: str | None = None
        self.device_rows: dict[str, Adw.ActionRow] = {}
        self.history_rows: list[Adw.ActionRow] = []
        GLib.timeout_add_seconds(4, self._refresh_connected_devices)

        self.toast_overlay = Adw.ToastOverlay()
        self.set_content(self.toast_overlay)

        toolbar_view = Adw.ToolbarView()
        self.toast_overlay.set_child(toolbar_view)

        # --- HeaderBar with Responsive ViewSwitcher ---
        header = Adw.HeaderBar()
        toolbar_view.add_top_bar(header)

        self.stack = Adw.ViewStack(enable_transitions=True)
        self.stack.connect("notify::visible-child-name", self._on_mode_changed)

        switcher = Adw.ViewSwitcher(
            stack=self.stack,
            policy=Adw.ViewSwitcherPolicy.WIDE,
        )
        header.set_title_widget(switcher)

        bottom_switcher = Adw.ViewSwitcherBar(stack=self.stack)
        toolbar_view.add_bottom_bar(bottom_switcher)

        bp = Adw.Breakpoint.new(Adw.BreakpointCondition.parse("max-width: 470sp"))
        bp.add_setter(switcher, "visible", False)
        bp.add_setter(bottom_switcher, "reveal", True)
        self.add_breakpoint(bp)

        regen_btn = Gtk.Button(
            icon_name="view-refresh-symbolic",
            tooltip_text="Generate New Session Token (Ctrl+R)",
        )
        regen_btn.connect("clicked", self._on_regenerate_token)
        header.pack_end(regen_btn)

        menu = Gio.Menu()
        menu.append("Get Android App…", "win.get-android-app")
        menu.append("Open Downloads Folder", "win.open-downloads")
        menu.append("About BeamDrop", "win.about")

        menu_btn = Gtk.MenuButton(
            icon_name="open-menu-symbolic",
            menu_model=menu,
            tooltip_text="Main Menu",
        )
        header.pack_end(menu_btn)
        self._setup_actions()
        self._setup_shortcuts()

        # --- Scrollable Clamped Layout ---
        scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER)
        toolbar_view.set_content(scroll)

        clamp = Adw.Clamp(maximum_size=480, tightening_threshold=360)
        scroll.set_child(clamp)

        main_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=18,
            margin_top=14, margin_bottom=24, margin_start=16, margin_end=16,
        )
        clamp.set_child(main_box)

        # Dynamic Status Pill
        self.net_pill = Gtk.Label(
            label=f"●  Wi-Fi Active  •  {self.lan_ip}:{self.server.port}",
            halign=Gtk.Align.CENTER,
        )
        self.net_pill.add_css_class("network-badge")
        main_box.append(self.net_pill)

        # --- Animated Connected Android Devices Section ---
        self.devices_revealer = Gtk.Revealer(
            transition_type=Gtk.RevealerTransitionType.SLIDE_DOWN,
            transition_duration=260,
            reveal_child=False,
        )
        self.devices_group = Adw.PreferencesGroup(title="Connected Devices")
        self.devices_revealer.set_child(self.devices_group)
        main_box.append(self.devices_revealer)

        # --- Animated Live Transfer Progress Card ---
        self.progress_revealer = Gtk.Revealer(
            transition_type=Gtk.RevealerTransitionType.SLIDE_DOWN,
            transition_duration=240,
            reveal_child=False,
        )
        self.progress_group = Adw.PreferencesGroup(title="Active Transfer")
        self.progress_row = Adw.ActionRow(title="Transferring…", subtitle="0.0 MB/s")
        self.progress_row.add_css_class("tabular-stats")
        self.progress_icon = Gtk.Image.new_from_icon_name("document-send-symbolic")
        self.progress_row.add_prefix(self.progress_icon)

        self.progress_bar = Gtk.ProgressBar(valign=Gtk.Align.CENTER, width_request=120)
        self.progress_bar.add_css_class("transfer-progress")
        self.progress_row.add_suffix(self.progress_bar)
        self.progress_group.add(self.progress_row)
        self.progress_revealer.set_child(self.progress_group)
        main_box.append(self.progress_revealer)

        spring_target = Adw.PropertyAnimationTarget.new(self.progress_bar, "fraction")
        self.progress_anim = Adw.SpringAnimation.new(
            self.progress_bar, 0.0, 0.0,
            Adw.SpringParams.new(1.0, 0.4, 220.0),
            spring_target,
        )

        main_box.append(self.stack)

        # --- Page 1: Send Files & Folders ---
        self.send_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.send_box.set_valign(Gtk.Align.CENTER)
        self.send_box.add_css_class("drop-zone")

        self.send_status_revealer = Gtk.Revealer(
            transition_type=Gtk.RevealerTransitionType.CROSSFADE,
            transition_duration=220,
            reveal_child=True,
        )
        self.send_status = Adw.StatusPage(
            icon_name="document-send-symbolic",
            title="Send Files or Folders",
            description="Drop files or folders anywhere here, or pick from your device. Multiple items stream as a ZIP.",
        )
        self.send_status.add_css_class("compact")

        btn_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10, halign=Gtk.Align.CENTER)
        pick_files_btn = Gtk.Button(label="Choose Files…", tooltip_text="Ctrl+O")
        pick_files_btn.add_css_class("suggested-action")
        pick_files_btn.add_css_class("pill")
        pick_files_btn.connect("clicked", self._on_pick_files)
        btn_row.append(pick_files_btn)

        pick_folder_btn = Gtk.Button(label="Choose Folder…", tooltip_text="Ctrl+Shift+O")
        pick_folder_btn.add_css_class("pill")
        pick_folder_btn.connect("clicked", self._on_pick_folder)
        btn_row.append(pick_folder_btn)

        self.send_status.set_child(btn_row)
        self.send_status_revealer.set_child(self.send_status)
        self.send_box.append(self.send_status_revealer)

        self.send_qr_revealer = Gtk.Revealer(
            transition_type=Gtk.RevealerTransitionType.SLIDE_DOWN,
            transition_duration=280,
            reveal_child=False,
        )
        self.send_qr_frame = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=14,
            halign=Gtk.Align.CENTER,
        )
        self.send_qr_revealer.set_child(self.send_qr_frame)
        self.send_box.append(self.send_qr_revealer)

        drop_target = Gtk.DropTarget.new(Gdk.FileList, Gdk.DragAction.COPY)
        drop_target.connect("enter", self._on_drag_enter)
        drop_target.connect("leave", self._on_drag_leave)
        drop_target.connect("drop", self._on_files_dropped)
        self.send_box.add_controller(drop_target)

        self.stack.add_titled_with_icon(self.send_box, "send", "Send", "go-up-symbolic")

        # --- Page 2: Receive Files ---
        recv_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16, margin_top=6)
        recv_box.set_valign(Gtk.Align.CENTER)
        recv_box.set_halign(Gtk.Align.CENTER)

        self.recv_qr_container = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, halign=Gtk.Align.CENTER)
        recv_box.append(self.recv_qr_container)

        recv_dest_group = Adw.PreferencesGroup()
        dest_row = Adw.ActionRow(
            title="Destination Folder",
            subtitle=str(self.server.download_dir),
        )
        dest_icon = Gtk.Image.new_from_icon_name("folder-download-symbolic")
        dest_icon.add_css_class("icon-received")
        dest_row.add_prefix(dest_icon)

        open_dl_btn = Gtk.Button(
            icon_name="folder-open-symbolic",
            tooltip_text="Open Downloads Folder",
            valign=Gtk.Align.CENTER,
        )
        open_dl_btn.add_css_class("flat")
        open_dl_btn.connect("clicked", lambda *_: self._open_containing_folder(self.server.download_dir))
        dest_row.add_suffix(open_dl_btn)
        recv_dest_group.add(dest_row)
        recv_box.append(recv_dest_group)

        self.stack.add_titled_with_icon(recv_box, "receive", "Receive", "go-down-symbolic")

        # --- Page 3: Text & Links ---
        text_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14, margin_top=4)
        text_box.set_valign(Gtk.Align.CENTER)

        text_group = Adw.PreferencesGroup(
            title="Shared Text or Link",
            description="Type or paste below to make it available when scanned or synced.",
        )

        paste_btn = Gtk.Button(label="Paste", icon_name="edit-paste-symbolic")
        paste_btn.add_css_class("flat")
        paste_btn.connect("clicked", self._on_paste_clipboard)

        clear_text_btn = Gtk.Button(icon_name="edit-clear-symbolic", tooltip_text="Clear Text")
        clear_text_btn.add_css_class("flat")
        clear_text_btn.connect("clicked", lambda *_: self.text_buffer.set_text(""))

        header_suffix = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        header_suffix.append(paste_btn)
        header_suffix.append(clear_text_btn)
        text_group.set_header_suffix(header_suffix)

        text_card = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        text_card.add_css_class("boxed-list")

        text_scroll = Gtk.ScrolledWindow(min_content_height=86, max_content_height=125)
        self.text_view = Gtk.TextView(
            wrap_mode=Gtk.WrapMode.WORD_CHAR,
            top_margin=10, bottom_margin=10, left_margin=12, right_margin=12,
        )
        self.text_view.add_css_class("text-card")
        self.text_buffer = self.text_view.get_buffer()
        self.text_buffer.connect("changed", self._on_text_changed)
        text_scroll.set_child(self.text_view)
        text_card.append(text_scroll)
        text_group.add(text_card)
        text_box.append(text_group)

        self.text_qr_container = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, halign=Gtk.Align.CENTER)
        text_box.append(self.text_qr_container)

        self.stack.add_titled_with_icon(text_box, "text", "Text", "format-text-rich-symbolic")

        # --- Collapsible Transfer History ---
        history_group = Adw.PreferencesGroup()
        main_box.append(history_group)

        self.history_expander = Adw.ExpanderRow(
            title="Transfer History",
            subtitle="No transfers yet in this session",
            expanded=False,
        )
        self.history_expander.add_prefix(Gtk.Image.new_from_icon_name("document-open-recent-symbolic"))

        clear_btn = Gtk.Button(
            icon_name="user-trash-symbolic",
            tooltip_text="Clear History",
            valign=Gtk.Align.CENTER,
        )
        clear_btn.add_css_class("flat")
        clear_btn.connect("clicked", self._clear_history)
        self.history_expander.add_suffix(clear_btn)

        history_group.add(self.history_expander)

        self._build_receive_qr()
        self._build_text_qr()

    def _load_css(self):
        provider = Gtk.CssProvider()
        provider.load_from_data(APP_CSS.encode("utf-8"))
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(),
            provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )

    def _setup_actions(self):
        apk_action = Gio.SimpleAction.new("get-android-app", None)
        apk_action.connect("activate", self._on_get_android_app)
        self.add_action(apk_action)

        open_dl = Gio.SimpleAction.new("open-downloads", None)
        open_dl.connect("activate", lambda *_: self._open_containing_folder(self.server.download_dir))
        self.add_action(open_dl)

        about_action = Gio.SimpleAction.new("about", None)
        about_action.connect("activate", self._on_about)
        self.add_action(about_action)

    def _setup_shortcuts(self):
        controller = Gtk.ShortcutController()
        controller.set_scope(Gtk.ShortcutScope.MANAGED)

        shortcuts = [
            ("<Control>o", lambda *_: self._on_pick_files()),
            ("<Control><Shift>o", lambda *_: self._on_pick_folder()),
            ("<Control>r", lambda *_: self._on_regenerate_token()),
            ("<Alt>1", lambda *_: self.stack.set_visible_child_name("send")),
            ("<Alt>2", lambda *_: self.stack.set_visible_child_name("receive")),
            ("<Alt>3", lambda *_: self.stack.set_visible_child_name("text")),
        ]
        for accel, callback in shortcuts:
            trigger = Gtk.ShortcutTrigger.parse_string(accel)
            action = Gtk.CallbackAction.new(callback)
            controller.add_shortcut(Gtk.Shortcut.new(trigger, action))

        self.add_controller(controller)

    def _on_get_android_app(self, *_):
        dialog = Adw.Dialog(title="Get Android App", content_width=380, content_height=470)
        toolbar_view = Adw.ToolbarView()
        toolbar_view.add_top_bar(Adw.HeaderBar())
        dialog.set_child(toolbar_view)

        box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=14,
            margin_top=16, margin_bottom=24, margin_start=24, margin_end=24,
            valign=Gtk.Align.CENTER, halign=Gtk.Align.CENTER,
        )
        toolbar_view.set_content(box)

        apk_url = f"http://{self.lan_ip}:{self.server.port}/app.apk"

        qr_box = Gtk.Box(halign=Gtk.Align.CENTER)
        qr_box.add_css_class("qr-pass")
        qr_box.add_css_class("mode-receive")
        qr_box.append(make_qr_picture(apk_url, size_px=210))
        box.append(qr_box)

        title_lbl = Gtk.Label(label="Install BeamDrop on Android")
        title_lbl.add_css_class("title-3")
        box.append(title_lbl)

        if APK_PATH.exists():
            size_mb = APK_PATH.stat().st_size / (1024 * 1024)
            status_text = f"Scan with your phone's camera to download BeamDrop.apk ({size_mb:.1f} MB)."
        else:
            status_text = (
                "APK not built yet. Place your release APK at:\n"
                f"{APK_PATH}"
            )

        desc_lbl = Gtk.Label(label=status_text, justify=Gtk.Justification.CENTER, wrap=True, max_width_chars=36)
        desc_lbl.add_css_class("dim-label")
        box.append(desc_lbl)

        copy_btn = Gtk.Button(halign=Gtk.Align.CENTER)
        copy_btn.set_child(Adw.ButtonContent(icon_name="edit-copy-symbolic", label="Copy APK Link"))
        copy_btn.add_css_class("pill")
        copy_btn.add_css_class("btn-receive")
        copy_btn.connect("clicked", lambda *_: self._copy_text(apk_url, "Copied APK download URL"))
        box.append(copy_btn)

        dialog.present(self)

    def _on_about(self, *_):
        about = Adw.AboutDialog(
            application_name="BeamDrop",
            application_icon="io.github.myrnop.BeamDrop",
            developer_name="MyrnChef",
            version="0.2.0",
            website="https://github.com/myrnop/beamdrop",
            issue_url="https://github.com/myrnop/beamdrop/issues",
            license_type=Gtk.License.GPL_3_0,
            comments="Instant local Wi-Fi file, folder, and text sharing via QR code & Android companion.",
        )
        about.present(self)

    def _on_regenerate_token(self, *_):
        self.server.token = secrets.token_urlsafe(8)
        self.connected_device = None
        with self.server._lock:
            self.server.connected_devices.clear()
            self.server.pending_pushes.clear()
        self._refresh_connected_devices()
        self._update_status_badge()
        self._refresh_all_qrs()
        self._show_toast("Generated new session QR token")

    def _refresh_connected_devices(self) -> bool:
        now = datetime.datetime.now().timestamp()
        with self.server._lock:
            stale = [k for k, v in self.server.connected_devices.items() if now - v["last_seen"] > 10.0]
            for k in stale:
                del self.server.connected_devices[k]
            active = dict(self.server.connected_devices)

        for key in list(self.device_rows.keys()):
            if key not in active:
                self.devices_group.remove(self.device_rows.pop(key))

        for key, info in active.items():
            if key not in self.device_rows:
                row = Adw.ActionRow(
                    title=info["name"],
                    subtitle=f"Ready to receive  •  {info['ip']}",
                )
                phone_icon = Gtk.Image.new_from_icon_name("phone-symbolic")
                phone_icon.add_css_class("icon-received")
                row.add_prefix(phone_icon)

                send_btn = Gtk.Button(valign=Gtk.Align.CENTER)
                send_btn.set_child(Adw.ButtonContent(icon_name="document-send-symbolic", label="Send Files"))
                send_btn.add_css_class("suggested-action")
                send_btn.add_css_class("pill")
                send_btn.connect("clicked", lambda *_, dk=key, dn=info["name"]: self._on_push_to_device(dk, dn))
                row.add_suffix(send_btn)

                self.devices_group.add(row)
                self.device_rows[key] = row

        has_devices = bool(self.device_rows)
        self.devices_revealer.set_reveal_child(has_devices)
        if not has_devices and self.connected_device is not None:
            self.connected_device = None
            self._update_status_badge()
        return True

    def _on_push_to_device(self, device_key: str, device_name: str):
        dialog = Gtk.FileDialog(title=f"Send Files to {device_name}")
        dialog.open_multiple(self, None, lambda d, res: self._on_push_files_chosen(d, res, device_key, device_name))

    def _on_push_files_chosen(self, dialog, result, device_key: str, device_name: str):
        try:
            list_model = dialog.open_multiple_finish(result)
            paths = []
            for i in range(list_model.get_n_items()):
                gfile = list_model.get_item(i)
                if gfile and gfile.get_path():
                    paths.append(Path(gfile.get_path()))
            if paths:
                self.server.queue_push_for_device(device_key, paths)
                self._show_toast(f"Beaming {len(paths)} item(s) to {device_name}…")
        except Exception:
            pass

    def _update_status_badge(self):
        mode = self.stack.get_visible_child_name() or "send"
        self.net_pill.remove_css_class("mode-receive")
        self.net_pill.remove_css_class("mode-text")
        self.net_pill.remove_css_class("device-connected")

        if self.connected_device:
            self.net_pill.add_css_class("device-connected")
            self.net_pill.set_label(f"●  Connected: {self.connected_device}  •  :{self.server.port}")
        else:
            if mode in ("receive", "text"):
                self.net_pill.add_css_class(f"mode-{mode}")
            self.net_pill.set_label(f"●  Wi-Fi Active  •  {self.lan_ip}:{self.server.port}")

    def _refresh_all_qrs(self):
        self._build_receive_qr()
        self._build_text_qr()
        if self.server.shared_paths:
            self._set_shared_paths(self.server.shared_paths, switch_tab=False)

    def _current_url(self) -> str:
        return f"http://{self.lan_ip}:{self.server.port}/?t={self.server.token}"

    def _render_qr_card(self, container: Gtk.Box, url: str, title_text: str, subtitle_text: str, mode: str = "send", qr_size: int = 200):
        while child := container.get_first_child():
            container.remove(child)

        wrapper = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, halign=Gtk.Align.CENTER)

        qr_box = Gtk.Box(halign=Gtk.Align.CENTER)
        qr_box.add_css_class("qr-pass")
        if mode in ("receive", "text"):
            qr_box.add_css_class(f"mode-{mode}")

        qr_pic = make_qr_picture(url, size_px=qr_size)
        qr_box.append(qr_pic)
        wrapper.append(qr_box)

        info_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, halign=Gtk.Align.CENTER)
        title_lbl = Gtk.Label(label=title_text, ellipsize=3, max_width_chars=34)
        title_lbl.add_css_class("title-4")
        info_box.append(title_lbl)

        sub_lbl = Gtk.Label(label=subtitle_text, ellipsize=3, max_width_chars=40)
        sub_lbl.add_css_class("dim-label")
        sub_lbl.add_css_class("caption")
        info_box.append(sub_lbl)
        wrapper.append(info_box)

        copy_btn = Gtk.Button(halign=Gtk.Align.CENTER)
        btn_content = Adw.ButtonContent(icon_name="edit-copy-symbolic", label="Copy Link")
        copy_btn.set_child(btn_content)
        copy_btn.add_css_class("pill")
        if mode == "receive":
            copy_btn.add_css_class("btn-receive")
        elif mode == "text":
            copy_btn.add_css_class("btn-text")
        else:
            copy_btn.add_css_class("suggested-action")

        copy_btn.connect("clicked", lambda *_: self._copy_text(url, "Copied local URL to clipboard"))
        wrapper.append(copy_btn)

        container.append(wrapper)

    def _build_receive_qr(self):
        self._render_qr_card(
            self.recv_qr_container,
            self._current_url(),
            "Scan to Upload Files",
            "Point your phone's camera at the QR code",
            mode="receive",
            qr_size=205,
        )

    def _build_text_qr(self):
        self._render_qr_card(
            self.text_qr_container,
            self._current_url(),
            "Scan to Send or Copy Text",
            "Opens a live text & link pad on your phone",
            mode="text",
            qr_size=185,
        )

    def _on_mode_changed(self, *_):
        mode = self.stack.get_visible_child_name() or "send"
        self.server.mode = mode
        self._update_status_badge()

        if mode == "receive":
            self._build_receive_qr()
        elif mode == "text":
            self._build_text_qr()

    def _on_drag_enter(self, *_):
        self.send_box.add_css_class("drag-hover")
        return Gdk.DragAction.COPY

    def _on_drag_leave(self, *_):
        self.send_box.remove_css_class("drag-hover")

    def _on_text_changed(self, buf: Gtk.TextBuffer):
        start, end = buf.get_bounds()
        self.server.shared_text = buf.get_text(start, end, False)

    def _on_paste_clipboard(self, *_):
        self.get_clipboard().read_text_async(None, self._on_clipboard_read_done)

    def _on_clipboard_read_done(self, clipboard, result):
        try:
            text = clipboard.read_text_finish(result)
            if text:
                self.text_buffer.set_text(text)
                self._show_toast("Pasted from clipboard")
        except Exception:
            self._show_toast("Clipboard is empty or not text")

    def _on_pick_files(self, *_):
        dialog = Gtk.FileDialog(title="Select Files to Share")
        dialog.open_multiple(self, None, self._on_multiple_done)

    def _on_multiple_done(self, dialog, result):
        try:
            list_model = dialog.open_multiple_finish(result)
            paths = []
            for i in range(list_model.get_n_items()):
                gfile = list_model.get_item(i)
                if gfile and gfile.get_path():
                    paths.append(Path(gfile.get_path()))
            if paths:
                self._set_shared_paths(paths)
        except Exception:
            pass

    def _on_pick_folder(self, *_):
        dialog = Gtk.FileDialog(title="Select Folder to Share as ZIP")
        dialog.select_folder(self, None, self._on_folder_done)

    def _on_folder_done(self, dialog, result):
        try:
            gfile = dialog.select_folder_finish(result)
            if gfile and gfile.get_path():
                self._set_shared_paths([Path(gfile.get_path())])
        except Exception:
            pass

    def _on_files_dropped(self, target, value, x, y) -> bool:
        self.send_box.remove_css_class("drag-hover")
        if isinstance(value, Gdk.FileList):
            paths = [Path(f.get_path()) for f in value.get_files() if f.get_path()]
            if paths:
                self._set_shared_paths(paths)
                return True
        return False

    def _clear_shared_paths(self, *_):
        self.server.shared_paths = []
        self.send_qr_revealer.set_reveal_child(False)
        self.send_status_revealer.set_reveal_child(True)

    def _set_shared_paths(self, paths: list[Path], switch_tab: bool = True):
        valid_paths = [p for p in paths if p.exists()]
        if not valid_paths:
            self._show_toast("No valid files or folders selected")
            return

        self.server.shared_paths = valid_paths
        if switch_tab:
            self.server.mode = "send"

        self.send_status_revealer.set_reveal_child(False)
        self.send_qr_revealer.set_reveal_child(True)

        total_size = sum(_calculate_path_bytes(p) for p in valid_paths)
        size_str = _format_size(total_size)

        if len(valid_paths) == 1 and valid_paths[0].is_file():
            title_txt = valid_paths[0].name
            sub_txt = f"Single file  •  {size_str}"
        elif len(valid_paths) == 1 and valid_paths[0].is_dir():
            title_txt = f"{valid_paths[0].name}.zip"
            sub_txt = f"Folder ZIP stream  •  {size_str}"
        else:
            title_txt = f"{len(valid_paths)} items as ZIP"
            sub_txt = f"Multi-file ZIP stream  •  {size_str}"

        self._render_qr_card(self.send_qr_frame, self._current_url(), title_txt, sub_txt, mode="send", qr_size=195)

        # Expandable queue preview for multi-item or folder selections
        if len(valid_paths) > 1 or valid_paths[0].is_dir():
            queue_group = Adw.PreferencesGroup(width_request=320)
            expander = Adw.ExpanderRow(
                title="Queued Items",
                subtitle=f"{len(valid_paths)} item(s)  •  {size_str}",
            )
            expander.add_prefix(Gtk.Image.new_from_icon_name("folder-visiting-symbolic"))
            for p in valid_paths[:15]:
                item_row = Adw.ActionRow(
                    title=p.name,
                    subtitle=_format_size(_calculate_path_bytes(p)),
                )
                item_icon = Gtk.Image.new_from_icon_name(
                    "folder-symbolic" if p.is_dir() else "text-x-generic-symbolic"
                )
                item_row.add_prefix(item_icon)
                expander.add_row(item_row)
            queue_group.add(expander)
            self.send_qr_frame.append(queue_group)

        reset_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6, halign=Gtk.Align.CENTER)
        files_btn = Gtk.Button(label="Change Files…")
        files_btn.add_css_class("flat")
        files_btn.connect("clicked", self._on_pick_files)
        reset_box.append(files_btn)

        folder_btn = Gtk.Button(label="Change Folder…")
        folder_btn.add_css_class("flat")
        folder_btn.connect("clicked", self._on_pick_folder)
        reset_box.append(folder_btn)

        clear_btn = Gtk.Button(icon_name="edit-clear-symbolic", tooltip_text="Clear Selection")
        clear_btn.add_css_class("flat")
        clear_btn.connect("clicked", self._clear_shared_paths)
        reset_box.append(clear_btn)

        self.send_qr_frame.append(reset_box)

    def _animate_progress_to(self, target_fraction: float):
        current = self.progress_bar.get_fraction()
        self.progress_anim.set_value_from(current)
        self.progress_anim.set_value_to(target_fraction)
        self.progress_anim.play()

    def _on_transfer_event(self, record: dict):
        direction = record["direction"]

        if direction == "progress":
            name = record["name"]
            frac = record["fraction"]
            speed = record["speed_mbps"]
            done = record["done"]
            sub_dir = record["sub_direction"]

            self.progress_row.set_title(name)
            pct = int(frac * 100)
            speed_str = f"{speed:.1f} MB/s  •  {pct}%" if speed > 0 else f"{pct}%"
            self.progress_row.set_subtitle(speed_str)

            self.progress_icon.set_from_icon_name(
                "go-up-symbolic" if sub_dir == "sent" else "go-down-symbolic"
            )
            self.progress_revealer.set_reveal_child(True)
            self._animate_progress_to(frac)

            if done:
                GLib.timeout_add(900, lambda: self.progress_revealer.set_reveal_child(False) or False)
            return

        name = record["name"]
        timestamp = record["time"]

        if direction == "device":
            is_new = self.connected_device != name
            self.connected_device = name
            self._refresh_connected_devices()
            self._update_status_badge()
            if is_new:
                self._show_toast(f"Connected to {name}")
                row = Adw.ActionRow(title=name, subtitle=f"Android device connected at {timestamp}")
                icon = Gtk.Image.new_from_icon_name("phone-symbolic")
                icon.add_css_class("icon-received")
                row.add_prefix(icon)
                self.history_expander.add_row(row)
                self.history_rows.append(row)
                self._update_history_subtitle()
            return

        if direction == "text":
            snippet = record.get("text") or ""
            self.get_clipboard().set(snippet)
            self._show_toast("Copied incoming text from phone!")

            row = Adw.ActionRow(title=name, subtitle=f"Text received at {timestamp}")
            icon = Gtk.Image.new_from_icon_name("format-text-rich-symbolic")
            icon.add_css_class("icon-text")
            row.add_prefix(icon)

            copy_btn = Gtk.Button(
                icon_name="edit-copy-symbolic",
                tooltip_text="Copy Text",
                valign=Gtk.Align.CENTER,
            )
            copy_btn.add_css_class("flat")
            copy_btn.connect("clicked", lambda *_, t=snippet: self._copy_text(t, "Copied snippet to clipboard"))
            row.add_suffix(copy_btn)
        else:
            target_path: Path = record["path"]
            is_sent = direction == "sent"
            verb = "Sent" if is_sent else "Received"
            icon_name = "go-up-symbolic" if is_sent else "go-down-symbolic"

            self._show_toast(f"{verb}: {name}")

            row = Adw.ActionRow(title=name, subtitle=f"{verb} at {timestamp}")
            icon = Gtk.Image.new_from_icon_name(icon_name)
            icon.add_css_class("icon-sent" if is_sent else "icon-received")
            row.add_prefix(icon)

            open_btn = Gtk.Button(
                icon_name="folder-open-symbolic",
                tooltip_text="Show in File Manager",
                valign=Gtk.Align.CENTER,
            )
            open_btn.add_css_class("flat")
            open_btn.connect("clicked", lambda *_, p=target_path: self._open_containing_folder(p))
            row.add_suffix(open_btn)

        self.history_expander.add_row(row)
        self.history_rows.append(row)
        self.history_expander.set_expanded(True)
        self._update_history_subtitle()

    def _open_containing_folder(self, path: Path):
        target = path if path and path.exists() else self.server.download_dir
        gfile = Gio.File.new_for_path(str(target))
        launcher = Gtk.FileLauncher.new(gfile)
        if target.is_dir():
            launcher.launch(self, None, None)
        else:
            launcher.open_containing_folder(self, None, None)

    def _clear_history(self, *_):
        for row in self.history_rows:
            self.history_expander.remove(row)
        self.history_rows.clear()
        self.history_expander.set_expanded(False)
        self._update_history_subtitle()

    def _update_history_subtitle(self):
        count = len(self.history_rows)
        if count == 0:
            self.history_expander.set_subtitle("No transfers yet in this session")
        elif count == 1:
            self.history_expander.set_subtitle("1 transfer in this session")
        else:
            self.history_expander.set_subtitle(f"{count} transfers in this session")

    def _copy_text(self, text: str, toast_msg: str):
        self.get_clipboard().set(text)
        self._show_toast(toast_msg)

    def _show_toast(self, text: str):
        self.toast_overlay.add_toast(Adw.Toast(title=text))