# -*- coding: utf-8 -*-
"""
Interactive VNA test menu (standalone debugging tool).

Run: python testcode/vna_test.py
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pyvisa
import socket
from time import sleep

VISA_LIB = "visa32.dll"
DEFAULT_TIMEOUT = 120000


def list_resources(rm):
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


def build_hislip_addresses():
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


def try_connect(vna_address, label=""):
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
            print("[FAIL] PXI raw resource - SCPI not supported")
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
            print("[FAIL] Device busy (close VNA software)")
        elif "timeout" in err.lower():
            print("[FAIL] Connection timeout")
        else:
            print(f"[FAIL] Connection error: {e}")
        return None, False, ""


def save_s2p(vna, save_path=None):
    if save_path is None:
        save_path = os.path.join(os.getcwd(), "test_output.s2p")
    vna_safe = save_path.replace("\\", "/")
    print(f"\nSaving S2P to: {vna_safe}")
    try:
        print("  -> Stop continuous sweep")
        vna.write(":INIT:CONT OFF")
        print("  -> Trigger single sweep")
        vna.write(":INIT:IMM")
        print("  -> Wait for sweep...")
        try:
            vna.query("*OPC?")
        except Exception:
            sleep(5)
        print("  -> Saving file...")
        vna.write(f'MMEMory:STORe "{vna_safe}"')
        try:
            msg = vna.query(":SYSTem:ERRor?").strip()
            print(f"  VNA response: {msg}")
        except Exception:
            pass
        if os.path.exists(save_path):
            print(f"\nSuccess! File size: {os.path.getsize(save_path)} bytes")
            print(f"Location: {save_path}")
            return True
        else:
            print(f"\nFile not created: {save_path}")
            return False
    except Exception as e:
        print(f"\nSave failed: {e}")
        return False


def print_header():
    print("=" * 60)
    print("  VNA Standalone Test Script")
    print("  Note: PXI VNA requires HiSLIP address")
    print("=" * 60)


def print_menu():
    print("\n" + "-" * 60)
    print("  1. Scan all VISA devices")
    print("  2. Enter HiSLIP/TCPIP address manually")
    print("  3. Auto-try common HiSLIP addresses")
    print("  4. Save S2P test file (connect first)")
    print("  5. One-click full test")
    print("  6. Exit")
    print("-" * 60)


def main():
    vna = None
    rm = pyvisa.ResourceManager(VISA_LIB)

    while True:
        print_header()
        print_menu()
        choice = input("Select (1-6): ").strip()

        if choice == "1":
            list_resources(rm)
        elif choice == "2":
            addr = input("Enter HiSLIP/TCPIP address: ").strip()
            if addr:
                vna, is_pxi, conn_type = try_connect(addr)
                if is_pxi:
                    print("\nPXI device - SCPI not supported!")
                    vna = None
        elif choice == "3":
            candidates = build_hislip_addresses()
            print(f"\nTrying {len(candidates)} HiSLIP addresses...")
            for i, c in enumerate(candidates):
                print(f"\n--- Attempt {i+1}/{len(candidates)} ---")
                vna, is_pxi, conn_type = try_connect(c["addr"], c["label"])
                if vna and not is_pxi:
                    print(f"\nConnected! Address: {c['addr']}")
                    break
        elif choice == "4":
            if vna is None:
                print("Connect VNA first!")
            else:
                save_s2p(vna)
        elif choice == "5":
            print("\n=== One-click full test ===")
            list_resources(rm)
            candidates = build_hislip_addresses()
            print(f"\nAuto-trying {len(candidates)} HiSLIP addresses...")
            for i, c in enumerate(candidates):
                print(f"\n--- Attempt {i+1}/{len(candidates)} ---")
                vna, is_pxi, conn_type = try_connect(c["addr"], c["label"])
                if vna and not is_pxi:
                    print("\nConnected!")
                    break
            if vna and not is_pxi:
                save_s2p(vna)
            else:
                print("\nCould not connect to VNA")
        elif choice == "6":
            print("Exit")
            break
        else:
            print("Invalid option")

    if vna:
        try:
            vna.close()
        except Exception:
            pass


if __name__ == "__main__":
    main()
