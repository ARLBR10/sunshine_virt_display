"""
Connect and disconnect virtual displays by managing EDIDs and sysfs connector state.
"""

from __future__ import annotations

import time
from pathlib import Path

from src.drm import (
    find_empty_slot,
    force_crtc_assignment,
    get_card_name_from_device,
    get_connected_displays,
    get_drm_devices,
    release_crtc,
    run_command,
    wait_for_output_ready,
)
from src.drm.de.kwin import clear_kwin_output_config, disable_kwin_output
from src.edid import create_edid, find_best_vic_resolution, get_pixel_clock_info

SCRIPT_DIR = Path(__file__).parent.parent.absolute()


def _find_virtual_display_from_edid() -> tuple[str, str, Path] | None:
    """Find a connected output whose EDID matches this tool's generated EDID."""
    edid_file = SCRIPT_DIR / "custom_edid.bin"
    if not edid_file.exists():
        return None

    expected = edid_file.read_bytes()
    for drm_device in get_drm_devices():
        card_name = get_card_name_from_device(drm_device)
        for port in get_connected_displays(card_name):
            sysfs_edid = Path(f"/sys/class/drm/{card_name}-{port}/edid")
            try:
                if sysfs_edid.read_bytes() == expected:
                    return card_name, port, drm_device / port / "edid_override"
            except OSError:
                continue

    return None


def connect(width: int, height: int, refresh_rate: int, device: str | None = None) -> bool:
    """
    Connect a virtual display:
    1. Generate custom EDID
    2. Find empty display slot
    3. Override EDID
    4. Turn on virtual display
    5. Wait for output to be ready
    """
    print(f"Connecting virtual display: {width}x{height}@{refresh_rate}Hz")

    # If a previous session didn't clean up properly, the old virtual port still
    # has its EDID override set and sysfs status "connected". Clear it before
    # selecting a new empty slot.
    state_file = SCRIPT_DIR / "virt_display.state"
    if state_file.exists():
        stale = state_file.read_text().strip().split("\n")
        stale_card = stale[0] if len(stale) > 0 else ""
        stale_port = stale[1] if len(stale) > 1 else ""
        stale_edid = stale[3] if len(stale) > 3 else ""
        if stale_card and stale_port:
            print(f"  Stale session detected ({stale_card}-{stale_port}) — cleaning up...")
            if stale_edid:
                _ = run_command(f"sh -c 'cat /dev/null > {stale_edid}'")
            _ = run_command(f"sh -c 'echo off > /sys/class/drm/{stale_card}-{stale_port}/status'")
            time.sleep(0.5)  # let DRM process the hotplug before scanning
        state_file.unlink()

    # Step 1: Generate custom EDID
    print("Step 1: Generating custom EDID...")
    print(f"  Requested: {width}x{height} @ {refresh_rate}Hz")

    pixel_clock_mhz, max_mhz, will_break = get_pixel_clock_info(
        width, height, refresh_rate
    )
    print(f"  Pixel clock: {pixel_clock_mhz:.2f} MHz (max: {max_mhz:.2f} MHz)")

    if will_break:
        print(
            f"  ⚠️  WARNING: Pixel clock exceeds limit by {pixel_clock_mhz - max_mhz:.2f} MHz!"
        )
        print(f"  Finding best VIC standard resolution...")

        vic_result = find_best_vic_resolution(width, height, refresh_rate)
        if vic_result:
            vic_width, vic_height, vic_refresh, vic_code, vic_name = vic_result
            print(
                f"  → Falling back to VIC {vic_code}: {vic_width}x{vic_height} @ {vic_refresh}Hz ({vic_name})"
            )

            new_clock_mhz, _, _ = get_pixel_clock_info(
                vic_width, vic_height, vic_refresh
            )
            print(f"  → New pixel clock: {new_clock_mhz:.2f} MHz")

            width, height, refresh_rate = vic_width, vic_height, vic_refresh
        else:
            print(f"  ⚠️  No suitable VIC found, attempting custom resolution anyway...")
    else:
        print(f"  ✓ Pixel clock within limits")
        print(f"  ✓ Using custom resolution: {width}x{height} @ {refresh_rate}Hz")

    edid_data = create_edid(
        width=width,
        height=height,
        refresh_rate=refresh_rate,
        enable_hdr=True,
        display_name="Virtual Display",
    )

    edid_file = SCRIPT_DIR / "custom_edid.bin"
    _ = edid_file.write_bytes(edid_data)
    print(f"  ✓ Created EDID file: {edid_file}")
    print(f"  ✓ Final resolution: {width}x{height} @ {refresh_rate}Hz")
    print(f"  ✓ EDID size: {len(edid_data)} bytes")

    # Step 2: Find DRM devices and list connected displays
    print("\nStep 2: Scanning displays...")
    drm_devices = get_drm_devices()

    if not drm_devices:
        print("Error: No DRM devices found")
        return False

    if device:
        # User explicitly specified a card — find it or fail clearly.
        matched = [d for d in drm_devices if get_card_name_from_device(d) == device]
        if not matched:
            available = [get_card_name_from_device(d) for d in drm_devices]
            print(f"Error: device '{device}' not found. Available: {available}")
            return False
        drm_device = matched[0]
    else:
        # Pick the device that has the most connected displays — on multi-GPU
        # systems this ensures we land on the card with physical monitors rather
        # than an idle iGPU that happens to sort first by PCI address.
        best_device = drm_devices[0]
        best_count = -1
        for dev in drm_devices:
            c = get_card_name_from_device(dev)
            n = len(get_connected_displays(c))
            if n > best_count:
                best_count = n
                best_device = dev
        drm_device = best_device
    card_name = get_card_name_from_device(drm_device)
    print(f"  Using device: {drm_device.name} ({card_name})")

    connected_displays = get_connected_displays(card_name)
    print(
        f"  Connected displays: {connected_displays if connected_displays else 'None'}"
    )

    # Step 3: Find empty slot
    print("\nStep 3: Finding empty display slot...")
    empty_port, slot_device = find_empty_slot(drm_device, card_name)

    if not empty_port:
        print("Error: No empty display slots available")
        return False

    print(f"  ✓ Selected slot: {empty_port}")

    # Step 4: Override EDID
    print(f"\nStep 4: Overriding EDID for {empty_port}...")
    edid_override_path = slot_device / empty_port / "edid_override"

    cmd = f"sh -c 'cat {edid_file.absolute()} > {edid_override_path}'"
    result = run_command(cmd)

    if result.returncode != 0:
        print(f"  Error overriding EDID: {result.stderr}")
        return False

    print(f"  ✓ EDID override applied")

    print("\nStep 5: Leaving existing displays enabled")

    # Step 6: Clear any stale KWin output config, then turn on virtual display
    print(f"\nStep 6: Preparing virtual display ({empty_port})...")
    clear_kwin_output_config(empty_port)
    print(f"  Turning on virtual display ({empty_port})...")
    status_path = f"/sys/class/drm/{card_name}-{empty_port}/status"
    cmd = f"sh -c 'echo on > {status_path}'"
    result = run_command(cmd)

    if result.returncode != 0:
        print(f"  Error turning on display: {result.stderr}")
        return False

    print(f"  ✓ Virtual display enabled on {empty_port}")

    # Step 7: Wait for compositor to assign CRTC naturally, then fall back to forcing
    # a free CRTC. The DRM helper refuses to steal CRTCs already used by other outputs.
    print(f"\nStep 7: Waiting for output to be ready...")
    ready, mode = wait_for_output_ready(card_name, empty_port, width, height, timeout=5.0)

    if ready:
        print(f"  ✓ Output ready ({mode})")
    else:
        print(f"  ⚠ Compositor did not assign CRTC — trying a free CRTC...")
        forced = force_crtc_assignment(card_name, empty_port)
        ready, mode = wait_for_output_ready(card_name, empty_port, width, height, timeout=5.0)
        if forced and ready:
            print(f"  ✓ Output ready ({mode})")
        else:
            print("  ✗ Could not assign a free CRTC to the virtual display — cleaning up")
            _ = run_command(f"sh -c 'echo off > /sys/class/drm/{card_name}-{empty_port}/status'")
            _ = run_command(f"sh -c 'cat /dev/null > {edid_override_path}'")
            return False

    # Save only the virtual display state; physical displays are never disabled.
    state_file = SCRIPT_DIR / "virt_display.state"
    _ = state_file.write_text(f"{card_name}\n{empty_port}\n\n{edid_override_path}\n")

    print(f"\n✓ Virtual display successfully connected!")
    print(f"  Port: {card_name}-{empty_port}")
    print(f"  Resolution: {width}x{height}@{refresh_rate}Hz")

    return True


def disconnect() -> bool:
    """
    Disconnect virtual display:
    1. Release the virtual display CRTC
    2. Turn off virtual display
    3. Clear the EDID override
    """
    print("Disconnecting virtual display...")

    state_file = SCRIPT_DIR / "virt_display.state"
    if not state_file.exists():
        print("No state file found; scanning for matching virtual display EDID...")
        recovered = _find_virtual_display_from_edid()
        if not recovered:
            print("Error: No state file or matching virtual display found")
            return False
        card_name, virtual_port, edid_override_path = recovered
    else:
        state_data = state_file.read_text().strip().split("\n")
        if len(state_data) < 2:
            print("Error: Invalid state file")
            return False

        card_name = state_data[0]
        virtual_port = state_data[1]
        edid_override_path = state_data[3] if len(state_data) > 3 else ""

        if not edid_override_path:
            recovered = _find_virtual_display_from_edid()
            if recovered and recovered[0] == card_name and recovered[1] == virtual_port:
                edid_override_path = recovered[2]

    print(f"  Virtual display: {card_name}-{virtual_port}")

    print(f"\nStep 1: Disabling compositor output ({virtual_port})...")
    if not disable_kwin_output(virtual_port):
        print("  ⚠ KScreen disable was not available or did not accept the output")

    print(f"\nStep 2: Releasing CRTC from virtual display ({virtual_port})...")
    _ = release_crtc(card_name, virtual_port)

    print("\nStep 3: Clearing EDID override...")
    if edid_override_path:
        result = run_command(f"sh -c 'cat /dev/null > {edid_override_path}'")
        if result.returncode != 0:
            print(f"  Error: Could not clear EDID override: {result.stderr}")
            print("  State file preserved so disconnect can be retried")
            return False
        print(f"  ✓ EDID override cleared")
    else:
        print("  ⚠ No EDID override path in state file")

    print(f"\nStep 4: Turning off virtual display ({virtual_port})...")
    status_path = f"/sys/class/drm/{card_name}-{virtual_port}/status"
    result = run_command(f"sh -c 'echo off > {status_path}'")

    if result.returncode != 0:
        print(f"  Error: Could not turn off virtual display: {result.stderr}")
        print("  State file preserved so disconnect can be retried")
        return False

    # Some compositors keep the old CRTC alive until after the connector has
    # been hot-unplugged. Release again after echoing off to make removal stick.
    _ = release_crtc(card_name, virtual_port)

    sysfs_status = Path(status_path)
    disconnected = False
    for _ in range(10):
        try:
            if sysfs_status.read_text().strip() != "connected":
                disconnected = True
                break
        except OSError:
            disconnected = True
            break
        time.sleep(0.2)

    if not disconnected:
        print("  Error: Virtual display is still reported connected")
        print("  State file preserved so disconnect can be retried")
        return False

    print(f"  ✓ Virtual display turned off")

    if state_file.exists():
        state_file.unlink()

    print("\n✓ Virtual display disconnected!")
    return True
