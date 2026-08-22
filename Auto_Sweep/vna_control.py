# -*- coding: utf-8 -*-
"""
VNA (Vector Network Analyzer) control utilities.

Consolidated from power_sweep_auto.py and test_vna.py.
Handles:
  - S2P file saving (single-sweep trigger + MMEMory:STORe)
  - HiSLIP address discovery
  - VISA resource listing
  - Connection with error classification

Usage:
    from vna_control import save_s2p, try_connect

    vna, is_pxi, _ = try_connect("TCPIP0::localhost::hislip_...")
    if vna:
        save_s2p(vna, "D:/data/measurement.s2p")
"""

import os
import socket
from time import sleep
from typing import Optional, Tuple

import pyvisa

VISA_LIB = "visa32.dll"
DEFAULT_TIMEOUT = 120000


# =========================================================================
# S2P file save
# =========================================================================

def save_s2p(vna, save_path: str) -> bool:
    """Trigger a single VNA sweep and save the result as an .s2p file.

    SCPI sequence:
      1. :INIT:CONT OFF   — stop continuous sweep
      2. :INIT:IMM        — trigger single sweep
      3. *OPC?            — wait for completion
      4. MMEMory:STORe    — save to disk
      5. :SYSTem:ERRor?   — check for errors

    Returns True if the file was created on disk.
    """
    if vna is None:
        print("[Info] VNA not connected, skipping measurement")
        return False

    vna_safe_path = save_path.replace("\\", "/")
    print(f"Saving S2P to: {vna_safe_path}")

    try:
        vna.write(":INIT:CONT OFF")
        vna.write(":INIT:IMM")
        try:
            vna.query("*OPC?")
        except Exception:
            sleep(5)

        vna.write(f'MMEMory:STORe "{vna_safe_path}"')

        try:
            msg = vna.query(":SYSTem:ERRor?").strip()
            print(f"[VNA status] {msg}")
        except Exception:
            pass

        return os.path.exists(save_path)

    except Exception as e:
        print(f"[Warning] VNA operation failed: {e}")
        return False


# =========================================================================
# HiSLIP address discovery
# =========================================================================

def build_hislip_addresses() -> list:
    """Generate a list of candidate HiSLIP addresses to try.

    Returns a list of dicts with keys 'label' and 'addr'.
    Covers common chassis/slot/index combinations and hostname variants.
    """
    candidates = []

    candidates.append({
        "label": "Original",
        "addr": "TCPIP0::localhost::hislip_PXI10_CHASSIS1_SLOT1_INDEX0::INSTR",
    })
    candidates.append({
        "label": "localhost (alt)",
        "addr": "TCPIP0::localhost::hislip_PXI10_CHASSIS2_SLOT1_INDEX0::INSTR",
    })
    candidates.append({
        "label": "127.0.0.1",
        "addr": "TCPIP0::127.0.0.1::hislip_PXI10_CHASSIS2_SLOT1_INDEX0::INSTR",
    })

    try:
        hostname = socket.gethostname()
        candidates.append({
            "label": f"Hostname ({hostname})",
            "addr": f"TCPIP0::{hostname}::hislip_PXI10_CHASSIS2_SLOT1_INDEX0::INSTR",
        })
    except Exception:
        pass

    for chassis in [1, 2]:
        for slot in [1, 2]:
            for idx in range(3):
                candidates.append({
                    "label": f"CHASSIS{chassis}_SLOT{slot}_INDEX{idx}",
                    "addr": f"TCPIP0::localhost::hislip_PXI10_CHASSIS{chassis}_SLOT{slot}_INDEX{idx}::INSTR",
                })

    return candidates


# =========================================================================
# Connection
# =========================================================================

def try_connect(
    vna_address: str, label: str = ""
) -> Tuple[Optional[pyvisa.Resource], bool, str]:
    """Attempt to connect to a VNA and return (resource, is_pxi, conn_type).

    ``is_pxi`` is True if the address points to a raw PXI resource
    (which does NOT support SCPI — HiSLIP is required instead).

    Returns (None, False, "") on failure.
    """
    label_str = f" ({label})" if label else ""
    print(f"\nTrying VNA: {vna_address}{label_str}")

    rm = pyvisa.ResourceManager(VISA_LIB)

    try:
        vna = rm.open_resource(vna_address)
        vna.timeout = DEFAULT_TIMEOUT

        try:
            idn = vna.query("*IDN?").strip()
            print(f"[OK] VNA connected: {idn}")
            return vna, False, "HiSLIP/TCPIP"
        except AttributeError:
            print("[FAIL] PXI raw resource — SCPI not supported")
            print("  Use a HiSLIP address, not a PXI address!")
            vna.close()
            return None, True, "PXI (unsupported)"
        except Exception as e:
            print(f"[WARN] Connected but *IDN? failed: {e}")
            return vna, False, "HiSLIP (verify needed)"

    except Exception as e:
        err = str(e)
        if "RSRC_NFOUND" in err or "not present" in err:
            print("[FAIL] Device not found")
        elif "RSRC_BUSY" in err:
            print("[FAIL] Device busy (close VNA software first)")
        elif "timeout" in err.lower():
            print("[FAIL] Connection timeout")
        else:
            print(f"[FAIL] Connection error: {e}")
        return None, False, ""


# =========================================================================
# Resource listing
# =========================================================================

def list_resources(rm) -> list:
    """Scan and print all available VISA resources.

    Returns the raw list from ``rm.list_resources()``.
    """
    print("=" * 60)
    print("Scanning VISA resources...")
    print("=" * 60)

    try:
        resources = rm.list_resources()
        if not resources:
            print("No VISA resources found")
            return []

        tcpip, pxi, other = [], [], []
        for r in resources:
            if "TCPIP" in r.upper():
                tcpip.append(r)
            elif "PXI" in r.upper():
                pxi.append(r)
            else:
                other.append(r)

        if tcpip:
            print(f"\nTCPIP/HiSLIP devices ({len(tcpip)}):")
            for i, r in enumerate(tcpip):
                print(f"  [{i}] {r}")

        if pxi:
            print(f"\nPXI devices ({len(pxi)}):")
            print("  PXI devices need HiSLIP addresses!")
            for i, r in enumerate(pxi):
                print(f"  [{len(tcpip)+i}] {r}")

        if other:
            print(f"\nOther devices ({len(other)}):")
            for i, r in enumerate(other):
                print(f"  [{len(tcpip)+len(pxi)+i}] {r}")

        return resources
    except Exception as e:
        print(f"Scan failed: {e}")
        return []
