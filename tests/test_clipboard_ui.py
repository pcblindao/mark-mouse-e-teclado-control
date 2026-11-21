from types import SimpleNamespace
from unittest.mock import Mock

from markcontrol.gui.viewer import Viewer


def ui():
    return SimpleNamespace(photo=True, view_only=False, clip=True, pending_copy=False,
                           features={"clipboard_rich": True}, keys={17: "Control"},
                           status=Mock(), client=Mock())


def test_plain_ctrl_v_uses_rich_clipboard_without_forwarding_v():
    view = ui()
    view.paste = lambda: Viewer.paste(view)
    view.copy = Mock()
    key = SimpleNamespace(keycode=86, keysym="v", char="\x16", state=4)
    assert Viewer.key(view, key, True) == "break"
    Viewer.key(view, key, False)
    view.client.paste_clipboard.assert_called_once_with(paste_after=True)
    view.client.send.assert_not_called()
    assert view.keys == {17: "Control"}


def test_plain_ctrl_c_requests_copy_and_does_not_forward_c():
    view = ui()
    view.copy = lambda **kw: Viewer.copy(view, **kw)
    view.paste = Mock()
    key = SimpleNamespace(keycode=67, keysym="c", char="\x03", state=4)
    Viewer.key(view, key, True)
    Viewer.key(view, key, False)
    view.client.copy_clipboard.assert_called_once_with(copy_first=True)
    view.client.send.assert_not_called()


def test_disabled_clipboard_keeps_os_shortcut_without_reading_local_data():
    view = ui()
    view.clip = False
    key = SimpleNamespace(keycode=86, keysym="v", char="\x16", state=4)
    Viewer.key(view, key, True)
    view.client.paste_clipboard.assert_not_called()
    view.client.send.assert_called_once_with({"t": "key", "k": "v", "d": "down"})


def test_rich_clipboard_results_clear_pending_without_displaying_data():
    view = ui()
    view.pending_copy = True
    Viewer.clipboard_result(view, "clipboard_ok", {"direction": "copy", "kind": "files"})
    assert not view.pending_copy
    view.status.set.assert_called_with("Arquivos disponíveis para colar neste PC.")


def test_busy_copy_does_not_leave_copy_button_locked():
    view = ui()
    view.client.copy_clipboard.return_value = False
    Viewer.copy(view)
    assert not view.pending_copy
