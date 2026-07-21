"""Bot-menu GUI: the B-key dialog built from the engine's own widget tree.

Replaces the old ``sub_59B260`` text prompt + digit-key state machine with a
real modal window, constructed exactly like the in-game Esc quit dialog
(``sub_5BF240`` -> ``sub_46B050``; the confirm dialog ``sub_4721B0`` is the
closest template). Three emitted bodies:

- ``build_bot_menu`` — called from the dispatcher's B handler (inside its
  pushad). Guards on ``menu_open``, clones the base CWindow vtable into
  ``menu_vtable`` (overriding slot 0 = dtor and slot 21 = command handler),
  allocates + constructs the dialog, adds a title label and the mode-dependent
  buttons (DM/SK: one "Add Bot"; CTF: "Add Blue Bot" + "Add Red Bot"), plus a
  "Close" button, then shows it modally on the world manager (the same parent
  the Esc menu uses, ``*MANAGER_GLOBAL_VA``).

- ``menu_cmd`` — the dialog's vtable slot-21 notify handler. On a button
  activation (code == 0) it maps the widget to an action: spawn buttons set
  ``chosen_team`` and call ``do_spawn_with_team`` (keeping the menu open so the
  host can add several bots); the Close button dismisses the dialog via vtable
  slot 5. Mirrors the confirm dialog's ``sub_472330``.

- ``menu_dtor`` — the dialog's vtable slot-0 (deleting) destructor. Resets
  ``menu_open`` (so ANY close path frees the guard), then runs the base
  teardown + pooled free exactly like the confirm dialog's ``sub_472300``.

The button pointers live in scratch (``menu_btn0/1/2``) rather than object
fields so the object layout stays a plain 0x140-byte CWindow and needs no
private members beyond the base's.
"""

from .. import addresses as ax
from ..asm import Asm, le32
from ..layout import ScratchLayout

_NEG1000 = le32(0xFFFFFC18)  # push imm32 -1000 (auto-position sentinel)


def _emit_add_child(a: Asm, anchor: int) -> None:
    """Emit ``sub_40C7C0(ecx=EBX dialog, EDI child, anchor, 0, -1000, -1000, 0)``.

    Preconditions: EBX = dialog, EDI = the child widget. Clobbers caller regs
    per the ret-0x18 thiscall contract."""
    a.raw(b'\x6A\x00')                       # push 0            (a7 flags)
    a.raw(b'\x68' + _NEG1000)                # push -1000        (a6 y)
    a.raw(b'\x68' + _NEG1000)                # push -1000        (a5 x)
    a.raw(b'\x6A\x00')                       # push 0            (a4)
    a.raw(b'\x6A' + bytes([anchor]))         # push anchor       (a3)
    a.raw(b'\x57')                           # push edi          (a2 child)
    a.raw(b'\x89\xD9')                       # mov ecx, ebx      (this = dialog)
    a.call_va(ax.WIDGET_ADD_CHILD_VA)        # ret 0x18


def _emit_menu_button(a: Asm, text_va: int, dest_va: int, tag: str) -> None:
    """Allocate a push button, store its ptr in ``dest_va``, add it below the
    previous sibling. EBX must hold the dialog; EDI is used as the button reg."""
    skip = f'bm_btn_{tag}_skip'
    a.raw(b'\xB9' + le32(0x138))             # mov ecx, 0x138  (button size)
    a.call_va(ax.WIDGET_ALLOC_VA)
    a.raw(b'\x85\xC0'); a.jz(skip)
    a.raw(b'\x89\xC7')                       # mov edi, eax    (button)
    a.raw(b'\x6A\x00')                       # push 0          (a4)
    a.raw(b'\x68' + le32(text_va))           # push text       (a3)
    a.raw(b'\x53')                           # push ebx        (a2 parent = dialog)
    a.raw(b'\x89\xF9')                       # mov ecx, edi    (this = button)
    a.call_va(ax.BUTTON_CTOR_VA)             # ret 0xC
    a.raw(b'\x89\x3D' + le32(dest_va))       # mov [dest], edi
    _emit_add_child(a, ax.WIDGET_ANCHOR_BELOW)
    a.label(skip)


def emit(a: Asm, layout: ScratchLayout) -> None:
    menu_open_va   = layout.va('menu_open')
    menu_btn0_va   = layout.va('menu_btn0')
    menu_btn1_va   = layout.va('menu_btn1')
    menu_btn2_va   = layout.va('menu_btn2')
    menu_vtable_va = layout.va('menu_vtable')
    menu_parent_va = layout.va('menu_parent')
    menu_mode_va   = layout.va('menu_mode')
    chosen_team_va = layout.va('chosen_team')
    str_title_va   = layout.va('menu_str_title')
    str_addbot_va  = layout.va('menu_str_addbot')
    str_blue_va    = layout.va('menu_str_blue')
    str_red_va     = layout.va('menu_str_red')
    str_close_va   = layout.va('menu_str_close')

    # =======================================================================
    # build_bot_menu — construct + show the dialog. No args; reads menu_mode.
    # Preconditions: caller did pushad (dispatcher B handler).
    # =======================================================================
    a.label('build_bot_menu')
    a.raw(b'\x83\x3D' + le32(menu_open_va) + b'\x00')      # cmp [menu_open], 0
    a.jnz('bm_ret')                                        # already open
    # Resolve the desktop root widget = *(uimgr + 0x34) and stash it as the
    # parent for both the base ctor and the modal show. The world manager is a
    # CGame (not a CWindow), so it is NOT a valid parent — see addresses.py.
    a.raw(b'\xA1' + le32(ax.UI_MANAGER_GLOBAL_VA))         # mov eax, [uimgr]
    a.raw(b'\x85\xC0'); a.jz('bm_ret')
    a.raw(b'\x8B\x40' + bytes([ax.UI_DESKTOP_ROOT_OFF]))   # mov eax, [eax+0x34]  (desktop)
    a.raw(b'\x85\xC0'); a.jz('bm_ret')
    a.raw(b'\xA3' + le32(menu_parent_va))                  # mov [menu_parent], eax

    # Clone the base CWindow vtable into scratch, then override slot 0 (dtor)
    # and slot 21 (command handler). rep movsd clobbers esi/edi/ecx.
    a.raw(b'\xBE' + le32(ax.WIN_BASE_VTABLE_VA))           # mov esi, base vtable
    a.raw(b'\xBF' + le32(menu_vtable_va))                  # mov edi, menu_vtable
    a.raw(b'\xB9' + le32(ax.WIDGET_VTABLE_DWORDS))         # mov ecx, 77
    a.raw(b'\xFC\xF3\xA5')                                 # cld ; rep movsd
    a.raw(b'\xC7\x05' + le32(menu_vtable_va + ax.WIDGET_DTOR_VTBL_OFF))
    a.imm32_lbl('menu_dtor')                               # menu_vtable[0]    = &menu_dtor
    a.raw(b'\xC7\x05' + le32(menu_vtable_va + ax.WIDGET_CMD_VTBL_OFF))
    a.imm32_lbl('menu_cmd')                                # menu_vtable[0x54] = &menu_cmd

    # Reset the per-open button latches.
    a.raw(b'\xC7\x05' + le32(menu_btn0_va) + le32(0))
    a.raw(b'\xC7\x05' + le32(menu_btn1_va) + le32(0))
    a.raw(b'\xC7\x05' + le32(menu_btn2_va) + le32(0))

    # Allocate the dialog, run the base ctor (parent = mgr), set our vtable.
    a.raw(b'\xB9' + le32(ax.DIALOG_OBJ_SIZE))              # mov ecx, 0x140
    a.call_va(ax.WIDGET_ALLOC_VA)
    a.raw(b'\x85\xC0'); a.jz('bm_ret')
    a.raw(b'\x89\xC3')                                     # mov ebx, eax    (dialog)
    a.raw(b'\x6A\x00')                                     # push 0          (a3)
    a.raw(b'\xFF\x35' + le32(menu_parent_va))             # push [menu_parent] (a2 desktop)
    a.raw(b'\x89\xD9')                                     # mov ecx, ebx
    a.call_va(ax.WIN_BASE_CTOR_VA)                         # ret 8
    a.raw(b'\xC7\x03' + le32(menu_vtable_va))              # mov [ebx], menu_vtable

    # Title label (anchor 1). On alloc failure just skip it.
    a.raw(b'\xB9' + le32(0x128))                           # mov ecx, 0x128  (label size)
    a.call_va(ax.WIDGET_ALLOC_VA)
    a.raw(b'\x85\xC0'); a.jz('bm_title_skip')
    a.raw(b'\x89\xC7')                                     # mov edi, eax    (label)
    a.raw(b'\x68' + le32(str_title_va))                   # push text       (a3)
    a.raw(b'\x53')                                         # push ebx        (a2 parent)
    a.raw(b'\x89\xF9')                                     # mov ecx, edi
    a.call_va(ax.LABEL_CTOR_VA)                            # ret 8
    _emit_add_child(a, ax.WIDGET_ANCHOR_TITLE)
    a.label('bm_title_skip')

    # Spawn buttons by mode: CTF gets Blue+Red, DM/SK get a single Add Bot.
    a.raw(b'\x83\x3D' + le32(menu_mode_va) + b'\x01')      # cmp [menu_mode], 1 (CTF)
    a.jz('bm_ctf')
    _emit_menu_button(a, str_addbot_va, menu_btn0_va, 'addbot')
    a.jmp('bm_close_btn')
    a.label('bm_ctf')
    _emit_menu_button(a, str_blue_va, menu_btn0_va, 'blue')
    _emit_menu_button(a, str_red_va,  menu_btn1_va, 'red')
    a.label('bm_close_btn')
    _emit_menu_button(a, str_close_va, menu_btn2_va, 'close')

    # Keyboard default = first spawn button (Enter adds a bot). Guarded so a
    # failed alloc never hands sub_40CA40 a NULL.
    a.raw(b'\x83\x3D' + le32(menu_btn0_va) + b'\x00')      # cmp [menu_btn0], 0
    a.jz('bm_skip_default')
    a.raw(b'\xFF\x35' + le32(menu_btn0_va))               # push [menu_btn0]
    a.raw(b'\x89\xD9')                                     # mov ecx, ebx
    a.call_va(ax.WIDGET_SET_DEFAULT_VA)                    # ret 4
    a.label('bm_skip_default')

    # Show it modally on the world manager (centered), latch menu_open.
    a.raw(b'\x6A' + bytes([ax.WIDGET_SHOW_ANCHOR]))       # push 6          (a3 anchor)
    a.raw(b'\x53')                                         # push ebx        (a2 dialog)
    a.raw(b'\x8B\x0D' + le32(menu_parent_va))             # mov ecx, [menu_parent] (this = desktop)
    a.call_va(ax.WIDGET_SHOW_MODAL_VA)                     # ret 8
    a.raw(b'\xC7\x05' + le32(menu_open_va) + le32(1))     # menu_open = 1
    a.label('bm_ret')
    a.raw(b'\xC3')                                         # ret

    # =======================================================================
    # menu_cmd — vtable slot 21 notify handler.
    # __thiscall(ecx = dialog, [esp+4] = widget, [esp+8] = code); ret 8.
    # ECX (dialog) is preserved through the compares so the close path can
    # reach the vtable. Mirrors sub_472330's shape.
    # =======================================================================
    a.label('menu_cmd')
    a.raw(b'\x8B\x44\x24\x08')                             # mov eax, [esp+8]  (code)
    a.raw(b'\x85\xC0'); a.jnz('mc_retcode')               # code != 0 -> return code
    a.raw(b'\x8B\x44\x24\x04')                             # mov eax, [esp+4]  (widget)
    a.raw(b'\x3B\x05' + le32(menu_btn2_va))               # cmp eax, [menu_btn2]
    a.jz('mc_close')
    a.raw(b'\x3B\x05' + le32(menu_btn0_va))               # cmp eax, [menu_btn0]
    a.jz('mc_spawn0')
    a.raw(b'\x3B\x05' + le32(menu_btn1_va))               # cmp eax, [menu_btn1]
    a.jz('mc_spawn1')
    a.raw(b'\x31\xC0')                                     # xor eax, eax   (unhandled)
    a.raw(b'\xC2\x08\x00')                                 # ret 8

    a.label('mc_spawn0')
    a.raw(b'\xC7\x05' + le32(chosen_team_va) + le32(0))    # chosen_team = 0 (Blue / Add Bot)
    a.jmp('mc_dospawn')
    a.label('mc_spawn1')
    a.raw(b'\xC7\x05' + le32(chosen_team_va) + le32(1))    # chosen_team = 1 (Red)
    a.label('mc_dospawn')
    # do_spawn_with_team expects a pushad'd caller and reads menu_mode +
    # chosen_team from scratch. Keep the menu open (no close) so the host can
    # add several bots from one dialog.
    a.raw(b'\x60')                                         # pushad
    a.call_lbl('do_spawn_with_team')
    a.raw(b'\x61')                                         # popad
    a.raw(b'\x31\xC0')                                     # xor eax, eax
    a.raw(b'\xC2\x08\x00')                                 # ret 8

    a.label('mc_close')
    a.raw(b'\x8B\x01')                                     # mov eax, [ecx]  (vtable)
    a.raw(b'\xFF\x50' + bytes([ax.WIDGET_CLOSE_VTBL_OFF])) # call [eax+0x14] (close; -> menu_dtor)
    a.raw(b'\x31\xC0')                                     # xor eax, eax
    a.raw(b'\xC2\x08\x00')                                 # ret 8

    a.label('mc_retcode')
    a.raw(b'\xC2\x08\x00')                                 # ret 8  (eax = code)

    # =======================================================================
    # menu_dtor — vtable slot 0 (deleting) destructor.
    # __thiscall(ecx = dialog, [esp+4] = char flag); ret 4.
    # Resets menu_open (so every teardown path frees the guard), then runs the
    # base teardown + pooled free, exactly like the confirm dialog's sub_472300.
    # =======================================================================
    a.label('menu_dtor')
    a.raw(b'\x56')                                         # push esi
    a.raw(b'\x89\xCE')                                     # mov esi, ecx   (this)
    a.raw(b'\xC7\x05' + le32(menu_open_va) + le32(0))     # menu_open = 0
    a.raw(b'\xC7\x05' + le32(menu_btn0_va) + le32(0))
    a.raw(b'\xC7\x05' + le32(menu_btn1_va) + le32(0))
    a.raw(b'\xC7\x05' + le32(menu_btn2_va) + le32(0))
    a.raw(b'\x89\xF1')                                     # mov ecx, esi
    a.call_va(ax.WIN_BASE_TEARDOWN_VA)                    # sub_403D70(this); plain ret
    a.raw(b'\xF6\x44\x24\x08\x01')                         # test byte [esp+8], 1  (delete flag)
    a.jz('md_nofree')
    a.raw(b'\xBA' + le32(ax.DIALOG_OBJ_SIZE))             # mov edx, 0x140
    a.raw(b'\x89\xF1')                                     # mov ecx, esi
    a.call_va(ax.WIN_POOL_FREE_VA)                        # __fastcall free(this, 0x140)
    a.label('md_nofree')
    a.raw(b'\x89\xF0')                                     # mov eax, esi
    a.raw(b'\x5E')                                         # pop esi
    a.raw(b'\xC2\x04\x00')                                 # ret 4
