"""WM_KEYDOWN dispatcher.

Reached from the patched ``call sub_599580`` site with ECX = VK code. On every
key:
- key B -> MP gate -> ``detect_mode`` -> ``build_bot_menu`` (the graphical bot
  menu; hook/bot_menu.py). Bots are spawned by the dialog's buttons, not by a
  digit key — the old text-prompt + digit state machine it replaced is gone.
- key R -> MP gate -> ``do_snapshot`` (Phase A diff oracle)
- keys N/J/X/, -> MP-gated waypoint editor (drop / select / delete / save)
- key O -> toggle the authoring overlay

All paths tail-jmp to ``sub_599580`` so normal key handling is unaffected.
NOTE: the ``menu_state`` / ``prompts_table`` / ``max_for_mode`` / ``prompt_*``
scratch fields are vestigial reserved space from that removed text menu."""

from .. import addresses as ax
from .. import config as cfg
from ..asm import Asm, le32
from ..layout import ScratchLayout
from .helpers import mp_gate


def emit(a: Asm, layout: ScratchLayout) -> None:
    menu_mode_va     = layout.va('menu_mode')
    overlay_enabled_va = layout.va('overlay_enabled')
    pickup_register_enabled_va = layout.va('pickup_register_enabled')
    pickup_count_va = layout.va('pickup_count')

    # =======================================================================
    # Dispatcher entry: ECX = VK.
    # =======================================================================
    # --- B opens the bot menu; R takes a diagnostic snapshot
    # (R's snapshot includes the waypoint-graph diag chunks now;
    # no separate hotkey since W is bound to "move up" in-game).
    # N/J/X drive the waypoint editor (drop / select / delete);
    # O toggles the visual waypoint overlay for authoring.
    # ',' saves the current graph (load is automatic on match change).
    # S is bound to "move down" in-game so it can't be used for save. ---
    a.raw(b'\x80\xF9' + bytes([ax.VK_R])); a.jz('handle_R')
    a.raw(b'\x80\xF9' + bytes([ax.VK_N])); a.jz('handle_N')
    a.raw(b'\x80\xF9' + bytes([ax.VK_J])); a.jz('handle_J')
    a.raw(b'\x80\xF9' + bytes([ax.VK_X])); a.jz('handle_X')
    a.raw(b'\x80\xF9' + bytes([ax.VK_O])); a.jz('handle_overlay_toggle')
    a.raw(b'\x80\xF9' + bytes([ax.VK_COMMA])); a.jz('handle_save')
    a.raw(b'\x80\xF9' + bytes([ax.VK_B]))                    # cmp cl, VK_B
    a.jnz('passthru')

    # B opens the graphical bot menu (build_bot_menu, hook/bot_menu.py) instead
    # of the old on-screen text prompt. detect_mode picks the button set
    # (CTF => Blue/Red spawn buttons; DM/SK => a single Add Bot); build_bot_menu
    # self-guards on menu_open so a second B while the dialog is up is a no-op.
    a.raw(b'\x60')                                            # pushad
    mp_gate(a, 'pop_passthru')                                # mgr -> level -> mpd; mpd in eax
    a.call_lbl('detect_mode')                                 # eax = mode (0/1/2)
    a.raw(b'\xA3' + le32(menu_mode_va))                       # mov [menu_mode], eax
    a.call_lbl('build_bot_menu')                              # construct + show the GUI
    a.raw(b'\x61')                                            # popad
    a.jmp_va(ax.ORIG_TARGET_VA)

    a.label('pop_passthru')
    a.raw(b'\x61')                                             # popad
    a.label('passthru')
    a.jmp_va(ax.ORIG_TARGET_VA)

    # --- R-key snapshot handler (Phase A diff-dump) ---
    a.label('handle_R')
    a.raw(b'\x60')                                             # pushad
    mp_gate(a, 'pop_passthru')                                 # same chain as B handler
    a.call_lbl('do_snapshot')
    a.raw(b'\x61')                                             # popad
    a.jmp_va(ax.ORIG_TARGET_VA)

    # --- Waypoint-editor handlers (N drop, J select, X delete) ---
    # Each is gated by mp_gate so editing only fires inside an active match
    # (host char isn't a valid entity outside MP).
    a.label('handle_N')
    a.raw(b'\x60')
    mp_gate(a, 'pop_passthru')
    a.call_lbl('wp_drop')
    a.raw(b'\x61')
    a.jmp_va(ax.ORIG_TARGET_VA)

    a.label('handle_J')
    a.raw(b'\x60')
    mp_gate(a, 'pop_passthru')
    a.call_lbl('wp_select')
    a.raw(b'\x61')
    a.jmp_va(ax.ORIG_TARGET_VA)

    a.label('handle_X')
    a.raw(b'\x60')
    mp_gate(a, 'pop_passthru')
    a.call_lbl('wp_delete')
    a.raw(b'\x61')
    a.jmp_va(ax.ORIG_TARGET_VA)

    a.label('handle_save')
    a.raw(b'\x60')
    mp_gate(a, 'pop_passthru')
    a.call_lbl('wp_save')
    a.raw(b'\x61')
    a.jmp_va(ax.ORIG_TARGET_VA)

    a.label('handle_overlay_toggle')
    a.raw(b'\x60')
    mp_gate(a, 'pop_passthru')
    a.raw(b'\x83\x35' + le32(overlay_enabled_va) + b'\x01')   # xor dword [overlay_enabled], 1
    a.raw(b'\x83\x3D' + le32(overlay_enabled_va) + b'\x00')   # cmp [overlay_enabled], 0
    a.jz('overlay_msg_off')
    if cfg.PICKUP_OVERLAY_MARKERS_ENABLED:
        a.raw(b'\xC7\x05' + le32(pickup_register_enabled_va) + le32(1))
        a.raw(b'\xC7\x05' + le32(pickup_count_va) + le32(0))
    a.raw(b'\x6A\xFF')                                        # push -1
    a.raw(b'\x68'); a.imm32_lbl('wp_overlay_on_msg')           # push msg
    a.call_va(ax.SHOWMSG_VA)
    a.jmp('overlay_toggle_done')
    a.label('overlay_msg_off')
    if cfg.PICKUP_OVERLAY_MARKERS_ENABLED:
        pickup_base_enabled = 1 if (cfg.PICKUP_REGISTER_ENABLED or cfg.PICKUP_DIVERT_ENABLED) else 0
        a.raw(b'\xC7\x05' + le32(pickup_register_enabled_va) + le32(pickup_base_enabled))
        if not pickup_base_enabled:
            a.raw(b'\xC7\x05' + le32(pickup_count_va) + le32(0))
    a.raw(b'\x6A\xFF')                                        # push -1
    a.raw(b'\x68'); a.imm32_lbl('wp_overlay_off_msg')          # push msg
    a.call_va(ax.SHOWMSG_VA)
    a.label('overlay_toggle_done')
    a.raw(b'\x61')
    a.jmp_va(ax.ORIG_TARGET_VA)

    a.label('wp_overlay_on_msg')
    a.raw(b'[wp] overlay on\x00')
    a.label('wp_overlay_off_msg')
    a.raw(b'[wp] overlay off\x00')
