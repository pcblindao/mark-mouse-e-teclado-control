import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from markcontrol.gui import app, viewer
from markcontrol.gui.icons import logo


class GuiTests(unittest.TestCase):
    def test_capability_requires_both_ends(self):
        client = SimpleNamespace(send_file=Mock())
        for value in (None, "files", [], {"files": False}, {"files": "yes"}):
            self.assertIsNone(viewer.feature_method(client, value, "send_file"))
        self.assertIs(viewer.feature_method(client, ["files"], "send_file"), client.send_file)
        self.assertIsNone(viewer.feature_method(client, ["chat"], "send_chat"))

    def test_minimize_during_session_uses_hide(self):
        ui = app.App.__new__(app.App)
        ui.root, ui.tray, ui.done = Mock(), Mock(), threading.Event()
        ui.session_ip = "192.168.1.2"
        ui.root.state.return_value = "iconic"
        ui.root.after_idle.side_effect = lambda callback: callback()
        ui.queue_minimize(SimpleNamespace(widget=Mock()))
        ui.root.after_idle.assert_not_called()
        ui.tray.ready.is_set.return_value = False
        ui.queue_minimize(SimpleNamespace(widget=ui.root))
        ui.root.iconify.assert_not_called()
        ui.root.withdraw.assert_not_called()
        ui.tray.ready.is_set.return_value = True
        ui.queue_minimize(SimpleNamespace(widget=ui.root))
        ui.root.withdraw.assert_called_once()
        ui.root.deiconify.assert_not_called()

    def test_tray_fallback_and_single_notice(self):
        ui = SimpleNamespace(root=Mock(), tray=Mock(), session_ip="", notified=False)
        ui.tray.ready.is_set.return_value = False
        app.App.hide(ui)
        ui.root.iconify.assert_called_once()
        ui.tray.ready.is_set.return_value = True
        ui.tray.icon.HAS_NOTIFICATION = True
        app.App.hide(ui)
        app.App.hide(ui)
        self.assertEqual(ui.root.withdraw.call_count, 2)
        ui.tray.icon.notify.assert_called_once()

    def test_view_only_blocks_local_input(self):
        ui = SimpleNamespace(photo=True, view_only=True, clip=True, client=Mock())
        viewer.Viewer.key(ui, None, True)
        viewer.Viewer.pointer(ui, None, "move")
        viewer.Viewer.paste(ui)
        ui.client.send.assert_not_called()

    def test_preview_grabs_only_the_client_rectangle(self):
        from pathlib import Path
        from scripts import ui_preview
        window = Mock()
        window.winfo_width.return_value, window.winfo_height.return_value = 350, 520
        window.winfo_screenwidth.return_value, window.winfo_screenheight.return_value = 1280, 800
        window.winfo_rootx.return_value, window.winfo_rooty.return_value = 465, 140
        with patch.object(ui_preview.sys, "platform", "linux"), patch.object(ui_preview.ImageGrab, "grab") as grab:
            grab.return_value.size = (350, 520)
            ui_preview.capture(window, Path("02-vazio.png"))
        grab.assert_called_once_with(bbox=(465, 140, 815, 660), all_screens=True)
        grab.return_value.save.assert_called_once_with(Path("02-vazio.png"))
        window.withdraw.assert_called_once()

    def test_offline_preview_uses_seven_windows(self):
        import tkinter as tk
        from pathlib import Path
        from scripts import ui_preview
        try:
            probe = tk.Tk()
        except tk.TclError as exc:
            self.skipTest(str(exc))
        probe.withdraw()
        # Reuse the initialized interpreter: global offline mocks must not
        # interfere with loading another Tcl runtime on Windows runners.
        with patch.object(ui_preview.tk, "Tk", return_value=probe), \
                patch.object(Path, "mkdir"), patch.object(ui_preview, "capture") as capture, \
                patch("socket.socket", side_effect=AssertionError("network")), \
                patch("threading.Thread.start", side_effect=AssertionError("worker")):
            ui_preview.render(".")
        self.assertEqual([call.args[1].stem for call in capture.call_args_list], [
            "01-principal", "02-vazio", "03-configuracoes", "04-tela-remota",
            "05-sessao-ativa", "06-pin", "07-identidade",
        ])

    def test_logo_states(self):
        normal, active = logo(32), logo(32, active=True)
        self.assertEqual(normal.size, (32, 32))
        self.assertEqual(normal.getpixel((0, 0))[3], 0)
        self.assertNotEqual(normal.tobytes(), active.tobytes())

    def test_cards_and_dialogs_without_network(self):
        import tkinter as tk
        from markcontrol.gui.theme import install
        from markcontrol.gui.widgets import PeerList

        try:
            root = tk.Tk()
        except tk.TclError as exc:
            self.skipTest(str(exc))
        root.withdraw()
        self.addCleanup(root.destroy)
        install(root)
        peer = SimpleNamespace(name="Estúdio", ip="192.168.1.7", pin_required=True, fingerprint="ab:"*31+"ab")
        cards = PeerList(root, lambda _: peer, Mock())
        cards.insert("", "end", iid="pc", values=(peer.name, peer.ip, "Livre"))
        row = cards.rows["pc"].body
        self.assertTrue(row.grid_slaves(column=4)[0].enabled)
        self.assertTrue(row.grid_slaves(column=2))
        cards.item("pc", (peer.name, peer.ip, "Em uso"))
        self.assertFalse(row.grid_slaves(column=4)[0].enabled)
        cards.delete("pc")
        with patch.object(viewer, "Client") as client:
            client.return_value.closed = threading.Event()
            ui = viewer.Viewer(root, peer, {"quality": 65, "clipboard": True})
            ui.withdraw()
            ui.entry("Entrar com PIN", Mock(), True)
            self.assertEqual(ui.dialog.title(), f"{app.TITLE} · Digite o PIN")
            self.assertTrue(ui.dialog._mark_icon)
            self.assertTrue(ui.title().startswith(app.TITLE))
            self.assertTrue(ui._mark_icon)
            ui.identity(peer)
            self.assertIsInstance(ui.dialog, tk.Toplevel)
            self.assertEqual(ui.dialog.title(), f"{app.TITLE} · Este PC mudou de identidade")
            self.assertTrue(ui.dialog._mark_icon)
            self.assertFalse(ui.tools["file"][0].enabled)
            ui.destroy()
