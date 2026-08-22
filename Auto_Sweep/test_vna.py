# -*- coding: utf-8 -*-
"""
独立 VNA 连接与 S2P 保存测试脚本
PXI VNA 必须通过 HiSLIP (TCPIP) 访问，不能直接用 PXI 资源地址
"""

import os
import pyvisa
import socket
from time import sleep


VISA_LIB = "visa32.dll"
DEFAULT_TIMEOUT = 120000


def list_resources(rm, filters=None):
    """扫描并显示 VISA 资源"""
    print("=" * 60)
    print("扫描可用 VISA 资源...")
    print("=" * 60)
    try:
        resources = rm.list_resources()
        if not resources:
            print("未找到任何 VISA 资源")
            return []

        tcpip = []
        pxi = []
        other = []
        for r in resources:
            if "TCPIP" in r.upper():
                tcpip.append(r)
            elif "PXI" in r.upper():
                pxi.append(r)
            else:
                other.append(r)

        if tcpip:
            print(f"\n📡 TCPIP/HiSLIP 设备 ({len(tcpip)} 个):")
            for i, r in enumerate(tcpip):
                print(f"  [{i}] {r}")

        if pxi:
            print(f"\n🔌 PXI 设备 ({len(pxi)} 个):")
            print("  ⚠️ PXI 设备不支持 SCPI 命令，需用 HiSLIP 地址!")
            for i, r in enumerate(pxi):
                print(f"  [{len(tcpip)+i}] {r}")

        if other:
            print(f"\n📋 其它设备 ({len(other)} 个):")
            for i, r in enumerate(other):
                print(f"  [{len(tcpip)+len(pxi)+i}] {r}")

        return resources
    except Exception as e:
        print(f"扫描失败: {e}")
        return []


def build_hislip_addresses():
    """构建可能的 HiSLIP 地址列表"""
    candidates = []

    # 原始地址
    candidates.append({
        'label': '原始地址',
        'addr': 'TCPIP0::localhost::hislip_PXI10_CHASSIS1_SLOT1_INDEX0::INSTR'
    })

    # localhost 变体
    candidates.append({
        'label': 'localhost',
        'addr': 'TCPIP0::localhost::hislip_PXI10_CHASSIS2_SLOT1_INDEX0::INSTR'
    })

    candidates.append({
        'label': '127.0.0.1',
        'addr': 'TCPIP0::127.0.0.1::hislip_PXI10_CHASSIS2_SLOT1_INDEX0::INSTR'
    })

    # 尝试获取本机名
    try:
        hostname = socket.gethostname()
        candidates.append({
            'label': f'本机名 ({hostname})',
            'addr': f'TCPIP0::{hostname}::hislip_PXI10_CHASSIS2_SLOT1_INDEX0::INSTR'
        })
        ip = socket.gethostbyname(hostname)
        candidates.append({
            'label': f'本机IP ({ip})',
            'addr': f'TCPIP0::{ip}::hislip_PXI10_CHASSIS2_SLOT1_INDEX0::INSTR'
        })
    except:
        pass

    # 常见变体
    for chassis in [1, 2]:
        for slot in [1, 2]:
            for idx in range(3):
                candidates.append({
                    'label': f'CHASSIS{chassis}_SLOT{slot}_INDEX{idx}',
                    'addr': f'TCPIP0::localhost::hislip_PXI10_CHASSIS{chassis}_SLOT{slot}_INDEX{idx}::INSTR'
                })

    return candidates


def try_connect(vna_address, label=""):
    """尝试连接 VNA，返回 (vna, is_pxi, 连接方式描述)"""
    label_str = f" ({label})" if label else ""
    print(f"\n尝试连接 VNA: {vna_address}{label_str}")

    rm = pyvisa.ResourceManager(VISA_LIB)
    try:
        vna = rm.open_resource(vna_address)
        vna.timeout = DEFAULT_TIMEOUT

        try:
            idn = vna.query("*IDN?").strip()
            print(f"[OK] ✅ VNA 连接成功: {idn}")
            return vna, False, "HiSLIP/TCPIP"
        except AttributeError:
            # PXI 设备 - 不支持 SCPI，不能用
            print("[FAIL] ❌ 这是 PXI 原始资源，不支持 SCPI 命令")
            print("  需要 HiSLIP 地址而非 PXI 地址!")
            vna.close()
            return None, True, "PXI (不支持)"
        except Exception as e:
            print(f"[WARN] 连接成功但 *IDN? 失败: {e}")
            return vna, False, "HiSLIP (需验证)"

    except Exception as e:
        err = str(e)
        if "RSRC_NFOUND" in err or "not present" in err:
            print(f"[FAIL] ❌ 设备未找到")
        elif "RSRC_BUSY" in err:
            print(f"[FAIL] ❌ 设备被占用 (请关闭 VNA 软件)")
        elif "timeout" in err.lower():
            print(f"[FAIL] ❌ 连接超时")
        else:
            print(f"[FAIL] ❌ 连接失败: {e}")
        return None, False, ""


def save_s2p(vna, save_path=None):
    """保存 S2P 文件"""
    if save_path is None:
        save_path = os.path.join(os.getcwd(), "test_output.s2p")

    vna_safe_path = save_path.replace("\\", "/")
    print(f"\n📁 保存 S2P 到: {vna_safe_path}")

    try:
        print("  → 关闭连续扫描")
        vna.write(":INIT:CONT OFF")

        print("  → 触发单次扫描")
        vna.write(":INIT:IMM")

        print("  → 等待扫描完成...")
        try:
            vna.query("*OPC?")
        except:
            sleep(5)

        print("  → 保存文件...")
        vna.write(f'MMEMory:STORe "{vna_safe_path}"')

        print("  → 检查状态...")
        try:
            msg = vna.query(":SYSTem:ERRor?").strip()
            print(f"  VNA 反馈: {msg}")
        except:
            pass

        if os.path.exists(save_path):
            size = os.path.getsize(save_path)
            print(f"\n✅ 成功! 文件大小: {size} 字节")
            print(f"   位置: {save_path}")
            return True
        else:
            print(f"\n⚠️ 文件未生成: {save_path}")
            return False

    except Exception as e:
        print(f"\n❌ 保存失败: {e}")
        return False


def print_header():
    """打印标题"""
    print("=" * 60)
    print("  VNA 独立测试脚本")
    print("  说明: PXI VNA 必须通过 HiSLIP 地址访问")
    print("=" * 60)


def print_menu():
    """打印菜单"""
    print("\n" + "-" * 60)
    print("  1. 扫描所有 VISA 设备")
    print("  2. 手动输入 HiSLIP/TCPIP 地址连接")
    print("  3. 自动尝试常见 HiSLIP 地址")
    print("  4. 保存 S2P 测试文件 (需先连接)")
    print("  5. 一键完整测试")
    print("  6. 退出")
    print("-" * 60)


def main():
    vna = None
    rm = pyvisa.ResourceManager(VISA_LIB)

    while True:
        print_header()
        print_menu()
        choice = input("请输入选项 (1-6): ").strip()

        if choice == "1":
            list_resources(rm)

        elif choice == "2":
            addr = input("请输入 HiSLIP/TCPIP 地址: ").strip()
            if addr:
                vna, is_pxi, conn_type = try_connect(addr)
                if is_pxi:
                    print("\n⚠️ PXI 设备不支持 SCPI! 请使用 HiSLIP 地址：")
                    print("  格式: TCPIP0::<主机名或IP>::hislip_PXI10_CHASSIS2_SLOT1_INDEX0::INSTR")
                    vna = None

        elif choice == "3":
            candidates = build_hislip_addresses()
            print(f"\n将尝试 {len(candidates)} 个 HiSLIP 地址...")

            for i, c in enumerate(candidates):
                print(f"\n--- 尝试 {i+1}/{len(candidates)} ---")
                vna, is_pxi, conn_type = try_connect(c['addr'], c['label'])
                if vna and not is_pxi:
                    print(f"\n✅ 连接成功! 使用地址: {c['addr']}")
                    break

        elif choice == "4":
            if vna is None:
                print("❌ 请先连接 VNA!")
            else:
                save_s2p(vna)

        elif choice == "5":
            print("\n=== 一键完整测试 ===")

            # 1. 扫描
            list_resources(rm)

            # 2. 自动尝试 HiSLIP
            candidates = build_hislip_addresses()
            print(f"\n自动尝试 {len(candidates)} 个 HiSLIP 地址...")
            for i, c in enumerate(candidates):
                print(f"\n--- 尝试 {i+1}/{len(candidates)} ---")
                vna, is_pxi, conn_type = try_connect(c['addr'], c['label'])
                if vna and not is_pxi:
                    print(f"\n✅ 连接成功!")
                    break

            if vna and not is_pxi:
                # 3. 保存测试
                save_s2p(vna)
            else:
                print("\n❌ 未能连接 VNA")

        elif choice == "6":
            print("退出")
            break

        else:
            print("无效选项")

    if vna:
        try:
            vna.close()
        except:
            pass


if __name__ == "__main__":
    main()
