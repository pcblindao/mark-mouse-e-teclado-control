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

    def test_session_blocks_both_hide_paths(self):
        ui = SimpleNamespace(root=Mock(), session_ip="192.168.1.2")
        app.App.hide(ui)
        app.App.on_unmap(ui)
        self.assertEqual(ui.root.deiconify.call_count, 2)
        ui.root.withdraw.assert_not_called()
        ui.root.iconify.assert_not_called()

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
        cards.item("pc", (peer.name, peer.ip, "Em uso"))
        cards.delete("pc")
        with patch.object(viewer, "Client") as client:
            client.return_value.closed = threading.Event()
            ui = viewer.Viewer(root, peer, {"quality": 65, "clipboard": True})
            ui.withdraw()
            ui.entry("Entrar com PIN", Mock(), True)
            ui.identity(peer)
            self.assertEqual(ui.dialog.winfo_manager(), "place")
            self.assertFalse(ui.tools["file"][0].enabled)
            ui.destroy()
