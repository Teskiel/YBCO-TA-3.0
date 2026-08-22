# -*- coding: utf-8 -*-
"""
VNA Full Parameter Probe — Keysight P5003A
===========================================
Queries every readable parameter from the VNA and prints a structured dump.

Usage (on lab computer):
    C:\ProgramData\anaconda3\python.exe testcode\vna_probe.py

Or with a specific address:
    C:\ProgramData\anaconda3\python.exe testcode\vna_probe.py TCPIP0::localhost::hislip_PXI10_CHASSIS1_SLOT1_INDEX0::INSTR
"""

import sys
import pyvisa

VISA_LIB = "visa32.dll"
DEFAULT_VNA = "TCPIP0::localhost::hislip_PXI10_CHASSIS1_SLOT1_INDEX0::INSTR"


def probe_vna(vna_address: str = DEFAULT_VNA):
    rm = pyvisa.ResourceManager(VISA_LIB)
    vna = rm.open_resource(vna_address)
    vna.timeout = 15000
    vna.read_termination = "\n"

    print(f"Probing: {vna_address}\n")

    # Helper: try a query, return result or error string
    def q(cmd: str) -> str:
        try:
            result = vna.query(cmd).strip()
            # Handle "No error" for SYST:ERR?
            if cmd.startswith(":SYST:ERR") and result.startswith("+0,"):
                # Keep showing it
                pass
            return result
        except pyvisa.errors.VisaIOError as e:
            if "timeout" in str(e).lower():
                return "[TIMEOUT]"
            if "RSRC_NFOUND" in str(e):
                return "[NOT FOUND]"
            return f"[ERROR: {e}]"
        except Exception as e:
            return f"[ERROR: {e}]"

    # ===== IDENTITY & SYSTEM =====
    print("=" * 70)
    print("SYSTEM / IDENTITY")
    print("=" * 70)

    for label, cmd in [
        ("*IDN?              ", "*IDN?"),
        ("*OPT? (options)    ", "*OPT?"),
        ("SYST:VERS? (SCPI)  ", ":SYSTem:VERSion?"),
        ("SYST:ERR?          ", ":SYSTem:ERRor?"),
        ("SYST:DATE?         ", ":SYSTem:DATE?"),
        ("SYST:TIME?         ", ":SYSTem:TIME?"),
        ("SYST:PRES:TYPE?    ", ":SYSTem:PRESet:TYPE?"),
    ]:
        result = q(cmd)
        print(f"  {label} →  {result}")

    # ===== FREQUENCY =====
    print("\n" + "=" * 70)
    print("FREQUENCY SETTINGS")
    print("=" * 70)

    for label, cmd in [
        ("Center            ", ":SENSe:FREQuency:CENTer?"),
        ("Span              ", ":SENSe:FREQuency:SPAN?"),
        ("Start             ", ":SENSe:FREQuency:STARt?"),
        ("Stop              ", ":SENSe:FREQuency:STOP?"),
        ("CW Mode?          ", ":SENSe:SWEep:MODE?"),
        ("Frequency Mode    ", ":SENSe:FREQuency:MODE?"),
    ]:
        result = q(cmd)
        print(f"  {label} →  {result} Hz")

    # ===== SWEEP =====
    print("\n" + "=" * 70)
    print("SWEEP SETTINGS")
    print("=" * 70)

    for label, cmd in [
        ("Sweep Points      ", ":SENSe:SWEep:POINts?"),
        ("Sweep Time        ", ":SENSe:SWEep:TIME?"),
        ("Sweep Type        ", ":SENSe:SWEep:TYPE?"),
        ("Sweep Time:Auto?  ", ":SENSe:SWEep:TIME:AUTO?"),
        ("Dwell Time        ", ":SENSe:SWEep:DWELl?"),
        ("Trigger Source    ", ":TRIGger:SEQuence:SOURce?"),
        ("Trigger Scope     ", ":TRIGger:SEQuence:SCOPe?"),
        ("Sweep Direction   ", ":SENSe:FREQuency:SWEep:DIRection?"),
    ]:
        result = q(cmd)
        print(f"  {label} →  {result}")

    # ===== POWER =====
    print("\n" + "=" * 70)
    print("SOURCE / POWER SETTINGS")
    print("=" * 70)

    # The P5003A uses SOUR:POW<n> for each port
    for port in [1, 2, 3, 4]:
        result = q(f":SOURce:POWer{port}?")
        if "ERROR" not in result and "TIMEOUT" not in result:
            print(f"  Port {port} Power     →  {result} dBm")
        else:
            break  # try just SOUR:POW if port-specific fails

    for label, cmd in [
        ("Power Attenuator  ", ":SOURce:POWer:ATTenuation?"),
        ("Power Atten:Auto? ", ":SOURce:POWer:ATTenuation:AUTO?"),
        ("ALC Mode          ", ":SOURce:POWer:ALC:MODE?"),
        ("ALC State         ", ":SOURce:POWer:ALC?"),
        ("Source Atten      ", ":SOURce:POWer:ATTenuation?"),
        ("Power Level       ", ":SOURce:POWer?"),
    ]:
        result = q(cmd)
        if "ERROR" not in result and "TIMEOUT" not in result:
            print(f"  {label} →  {result}")

    # ===== IF BANDWIDTH =====
    print("\n" + "=" * 70)
    print("IF BANDWIDTH / DYNAMIC RANGE")
    print("=" * 70)

    for label, cmd in [
        ("IF Bandwidth      ", ":SENSe:BANDwidth?"),
        ("IF BW:Resolution  ", ":SENSe:BANDwidth:RESolution?"),
        ("IF BW:Auto?       ", ":SENSe:BANDwidth:AUTO?"),
    ]:
        result = q(cmd)
        print(f"  {label} →  {result} Hz")

    # ===== AVERAGING =====
    print("\n" + "=" * 70)
    print("AVERAGING & SMOOTHING")
    print("=" * 70)

    for label, cmd in [
        ("Averaging State   ", ":SENSe:AVERage:STATe?"),
        ("Averaging Count   ", ":SENSe:AVERage:COUNt?"),
        ("Averaging Mode    ", ":SENSe:AVERage:MODE?"),
        ("Smoothing State   ", ":SENSe:AVERage:SMOothing:STATe?"),
        ("Smoothing Aper    ", ":SENSe:AVERage:SMOothing:APERture?"),
    ]:
        result = q(cmd)
        print(f"  {label} →  {result}")

    # ===== TRACES / MEASUREMENTS =====
    print("\n" + "=" * 70)
    print("TRACES & MEASUREMENTS")
    print("=" * 70)

    trace_count_str = q(":CALCulate:PARameter:COUNt?")
    print(f"  Trace Count        →  {trace_count_str}")

    try:
        trace_count = int(trace_count_str) if trace_count_str.isdigit() else 0
    except (ValueError, TypeError):
        trace_count = 0

    if trace_count > 0:
        for tr in range(1, trace_count + 1):
            defin = q(f":CALCulate{tr}:PARameter:DEFine?")
            name = q(f":CALCulate{tr}:PARameter:CATalog?")
            fmt = q(f":CALCulate{tr}:FORMat?")
            print(f"  Trace {tr} Define     →  {defin}")
            if name and "ERROR" not in name:
                print(f"  Trace {tr} Catalog    →  {name}")
            if fmt and "ERROR" not in fmt:
                print(f"  Trace {tr} Format     →  {fmt}")

    # ===== DISPLAY =====
    print("\n" + "=" * 70)
    print("DISPLAY SETTINGS")
    print("=" * 70)

    for label, cmd in [
        ("Window Count      ", ":DISPlay:WINDow:COUNt?"),
        ("Title             ", ":DISPlay:WINDow:TITLe:DATA?"),
        ("Enabled           ", ":DISPlay:ENABle?"),
    ]:
        result = q(cmd)
        print(f"  {label} →  {result}")

    # ===== WINDOW / TRACE DISPLAY (up to 4 windows) =====
    for win in range(1, 5):
        wc = q(":DISPlay:WINDow:COUNt?")
        try:
            win_count = int(wc) if wc.isdigit() else 1
        except (ValueError, TypeError):
            win_count = 1

        if win > win_count:
            break

        for tr in range(1, 13):  # up to 12 traces per window
            state = q(f":DISPlay:WINDow{win}:TRACe{tr}:STATe?")
            if "ERROR" in state or "TIMEOUT" in state:
                break
            if state == "1" or state == "ON":
                y_ref = q(f":DISPlay:WINDow{win}:TRACe{tr}:Y:RPOSition?")
                y_div = q(f":DISPlay:WINDow{win}:TRACe{tr}:Y:PDIVision?")
                ref_level = q(f":DISPlay:WINDow{win}:TRACe{tr}:Y:RLEVel?")
                auto_y = q(f":DISPlay:WINDow{win}:TRACe{tr}:Y:AUTO?")
                print(f"  Win{win}/Tr{tr}: ON")
                print(f"    Y Ref Pos   →  {y_ref}")
                print(f"    Y /Div      →  {y_div}")
                print(f"    Y Ref Level →  {ref_level}")
                print(f"    Y Auto      →  {auto_y}")

    # ===== CALIBRATION =====
    print("\n" + "=" * 70)
    print("CALIBRATION")
    print("=" * 70)

    for label, cmd in [
        ("Correction State  ", ":SENSe:CORRection:STATe?"),
        ("Cal Type          ", ":SENSe:CORRection:COLLect:METHod?"),
        ("Interpolation On? ", ":SENSe:CORRection:INTerpolate?"),
        ("Cal Kit Label     ", ":SENSe:CORRection:CKIT:LABel?"),
        ("Port Extensions   ", ":SENSe:CORRection:EXTension:STATe?"),
    ]:
        result = q(cmd)
        print(f"  {label} →  {result}")

    # ===== MARKERS (check if any are active) =====
    print("\n" + "=" * 70)
    print("MARKERS")
    print("=" * 70)

    for mkr in range(1, 11):
        state = q(f":CALCulate:MARKer{mkr}:STATe?")
        if "ERROR" in state:
            break
        if state == "1" or state == "ON":
            x_val = q(f":CALCulate:MARKer{mkr}:X?")
            y_val = q(f":CALCulate:MARKer{mkr}:Y?")
            print(f"  Marker {mkr}: ON  X={x_val}  Y={y_val}")
        else:
            print(f"  Marker {mkr}: OFF")

    # ===== LIMIT LINES =====
    print("\n" + "=" * 70)
    print("CHECK: LIMIT LINES / LIMIT TEST")
    print("=" * 70)
    for label, cmd in [
        ("Limit Test State  ", ":CALCulate:LIMit:STATe?"),
        ("Limit Fail?       ", ":CALCulate:LIMit:FAIL?"),
    ]:
        result = q(cmd)
        print(f"  {label} →  {result}")

    # ===== DATA FORMAT & TRANSFER =====
    print("\n" + "=" * 70)
    print("DATA FORMAT")
    print("=" * 70)
    for label, cmd in [
        ("Format (ASC/REAL) ", ":FORMat:DATA?"),
        ("Byte Order        ", ":FORMat:BORDer?"),
    ]:
        result = q(cmd)
        print(f"  {label} →  {result}")

    # ===== ELECTRICAL DELAY / PORTS =====
    print("\n" + "=" * 70)
    print("PORT PROPERTIES")
    print("=" * 70)
    for label, cmd in [
        ("Port Count        ", ":SYSTem:PORT:COUNt?"),
        ("Impedance (Ω)     ", ":SYSTem:IMPEDance?"),
    ]:
        result = q(cmd)
        print(f"  {label} →  {result}")

    # ===== ELECTRICAL DELAY PER PORT =====
    for port in [1, 2, 3, 4]:
        edelay = q(f":SENSe:CORRection:EDELay:PORT{port}?")
        if "ERROR" in edelay:
            break
        print(f"  Port {port} Elec Delay →  {edelay} s")

    vna.close()
    rm.close()

    print("\n" + "=" * 70)
    print("PROBE COMPLETE")
    print("=" * 70)


if __name__ == "__main__":
    addr = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_VNA
    probe_vna(addr)
