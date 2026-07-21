"""Bot-menu GUI scratch fields (the B-key dialog).

Appended at the very tail so no existing offset shifts. Holds the runtime
guard/latch state, a scratch copy of the base CWindow vtable (cloned at
menu-open time with slots 0/21 overridden — see ``hook/bot_menu.py``), and the
fixed button/title label strings (packed by ``static_data``)."""

from .model import ScratchField


def extend_menu(c):
    fields = c.fields
    overlay_fields = c.overlay_fields

    base = max(f.end for f in fields + overlay_fields)
    base = (base + 0xF) & ~0xF

    off = base

    def add(name, size, note=''):
        nonlocal off
        fields.append(ScratchField(name, off, size, note))
        off += size

    # Runtime latch + per-open button pointers. menu_open is the re-entrancy
    # guard: set 1 when the dialog is shown, reset 0 by the dialog destructor
    # (menu_dtor) so any close path — button, engine teardown, match end —
    # frees the guard. menu_btn0/1/2 are the live child button pointers the
    # command handler compares an activated widget against (0 = absent).
    add('menu_open', 0x04, 'gui: 1 while the bot-menu dialog is open (guard; reset by menu_dtor)')
    add('menu_btn0', 0x04, 'gui: first spawn button ptr (Add Bot / CTF Blue); 0 = none')
    add('menu_btn1', 0x04, 'gui: second spawn button ptr (CTF Red); 0 = absent (DM/SK)')
    add('menu_btn2', 0x04, 'gui: close button ptr; 0 = none')
    add('menu_parent', 0x04, 'gui: desktop root widget (parent for ctor + show) held across construction')
    # Scratch clone of the base CWindow vtable (WIN_BASE_VTABLE_VA). Filled at
    # menu-open by a rep-movsd, then slots 0 (dtor) and 21 (command handler)
    # are overridden to point at our menu_dtor / menu_cmd. 4-byte aligned by
    # the four dwords above.
    add('menu_vtable', 0x134, 'gui: dialog vtable = base CWindow clone + slot0/21 overrides')
    # Fixed label strings (NUL-terminated ASCII, packed by static_data). The
    # widget text setters copy them, so the scratch storage only needs to
    # outlive the copy.
    add('menu_str_title',  0x14, 'gui: window title label')
    add('menu_str_addbot', 0x14, 'gui: "Add Bot" button label (DM/SK)')
    add('menu_str_blue',   0x14, 'gui: "Add Blue Bot" button label (CTF)')
    add('menu_str_red',    0x14, 'gui: "Add Red Bot" button label (CTF)')
    add('menu_str_close',  0x14, 'gui: "Close" button label')
