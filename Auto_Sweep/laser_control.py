# -*- coding: utf-8 -*-
"""
激光器控制模块 - Laser Controller
用于Keysight N7779C激光器的控制和异常处理

功能：
1. 正常测量：功率设为0（保持连接）
2. 紧急情况：物理关闭激光器电源
3. 断联重连：20s → 40s → 60s 三次重试后物理关闭，再每20s检测重连3次
4. 重连后确认波长1500nm
"""

import pyvisa
from time import sleep


class LaserController:
    """Keysight N7779C 激光器控制器"""

    def __init__(self, resource_address: str = 'TCPIP0::100.65.11.65::INSTR'):
        self.resource_address = resource_address
        self.inst = None
        self.rm = None
        self.connected = False
        self.current_power = None
        self.target_wavelength = 1500  # nm

    # ==================== 连接管理 ====================

    def connect(self) -> bool:
        """建立连接"""
        try:
            self.rm = pyvisa.ResourceManager("visa32.dll")
            self.inst = self.rm.open_resource(self.resource_address)
            self.inst.timeout = 5000
            idn = self.inst.query('*IDN?').strip()
            print(f"[OK] 激光器连接成功: {idn}")
            self.connected = True
            return True
        except Exception as e:
            print(f"[Error] 激光器连接失败: {e}")
            self.connected = False
            return False

    def disconnect(self):
        """断开连接"""
        try:
            if self.inst:
                self.inst.close()
            if self.rm:
                self.rm.close()
        except:
            pass
        self.connected = False

    def is_connected(self) -> bool:
        """检查连接状态"""
        if not self.inst or not self.connected:
            return False
        try:
            self.inst.query('*IDN?')
            return True
        except:
            self.connected = False
            return False

    # ==================== 正常操作（不物理关闭） ====================

    def set_power(self, power_mw: float):
        """
        设置激光功率（正常测量使用）
        - 设置0mW时只关闭输出，不物理断开
        - 保持激光器连接状态
        """
        if not self.is_connected():
            print(f"[Warning] 激光器未连接，跳过功率设置")
            return False

        try:
            if power_mw == 0:
                # 只关闭输出，不物理断开
                self.inst.write(':OUTPut:STATe OFF')
                self.current_power = 0
                print(f"[OK] 激光功率设为 0 mW（输出关闭，保持连接）")
            else:
                # 设置功率并开启
                self.inst.write(f':SOURce:POWer {power_mw}MW')
                self.inst.write(':OUTPut:STATe ON')
                self.current_power = power_mw
                print(f"[OK] 激光功率设为 {power_mw} mW")
            return True
        except Exception as e:
            print(f"[Error] 设置激光功率失败: {e}")
            return False

    def get_power(self) -> float:
        """读取当前功率设置"""
        if not self.is_connected():
            return -1
        try:
            resp = self.inst.query(':SOURce:POWer?')
            return float(resp.strip())
        except:
            return -1

    # ==================== 紧急物理关闭 ====================

    def physical_off(self):
        """
        物理关闭激光器电源（仅紧急情况使用）
        完全断开激光器物理连接
        """
        print(f"[Warning] 执行物理关闭激光器电源...")
        try:
            if self.inst:
                # 发送关闭命令
                self.inst.write(':OUTPut:STATe OFF')
                sleep(1)
                # 物理断开连接
                self.inst.close()
                self.inst = None
            self.connected = False
            self.current_power = None
            print(f"[OK] 激光器电源已物理关闭")
        except Exception as e:
            print(f"[Error] 物理关闭失败: {e}")

    def physical_on(self):
        """
        物理重新开启激光器电源（紧急关闭后使用）
        """
        print(f"[Info] 尝试重新开启激光器电源...")
        if self.connect():
            # 确认波长
            self.set_wavelength(self.target_wavelength)
            print(f"[OK] 激光器电源已重新开启")
            return True
        return False

    # ==================== 波长控制 ====================

    def set_wavelength(self, wavelength_nm: float = 1500):
        """设置激光波长（默认1500nm）"""
        if not self.is_connected():
            return False
        try:
            self.inst.write(f':SOURce:WAV {wavelength_nm}NM')
            self.target_wavelength = wavelength_nm
            print(f"[OK] 激光波长设为 {wavelength_nm} nm")
            return True
        except Exception as e:
            print(f"[Error] 设置波长失败: {e}")
            return False

    def get_wavelength(self) -> float:
        """读取当前波长"""
        if not self.is_connected():
            return -1
        try:
            resp = self.inst.query(':SOURce:WAV?')
            return float(resp.strip())
        except:
            return -1

    # ==================== 状态读取 ====================

    def get_status(self) -> dict:
        """获取完整状态信息"""
        status = {
            'connected': self.is_connected(),
            'power_mw': self.get_power() if self.is_connected() else None,
            'wavelength_nm': self.get_wavelength() if self.is_connected() else None,
            'output_enabled': False
        }
        if self.is_connected():
            try:
                resp = self.inst.query(':OUTPut:STATe?')
                status['output_enabled'] = resp.strip() == '1'
            except:
                pass
        return status

    def print_status(self):
        """打印状态信息"""
        status = self.get_status()
        print("=" * 50)
        print("激光器状态:")
        print(f"  连接状态: {'已连接' if status['connected'] else '未连接'}")
        if status['connected']:
            print(f"  当前功率: {status['power_mw']} mW")
            print(f"  当前波长: {status['wavelength_nm']} nm")
            print(f"  输出状态: {'开启' if status['output_enabled'] else '关闭'}")
        print("=" * 50)

    # ==================== 断联重连处理 ====================

    def handle_disconnection(self) -> bool:
        """
        处理激光器断联
        策略：20s → 40s → 60s 三次重连，全部失败后物理关闭再重试

        Returns:
            bool: 是否成功恢复连接
        """
        print("[Warning] 检测到激光器断联！")

        # 第一阶段：尝试普通重连（20s, 40s, 60s）
        wait_times = [20, 40, 60]
        for i, wait in enumerate(wait_times):
            print(f"[Info] 等待 {wait} 秒后重试... ({i+1}/3)")
            sleep(wait)

            if self.connect():
                print(f"[OK] 重连成功！")
                # 确认波长
                self.set_wavelength(self.target_wavelength)
                return True
            else:
                print(f"[Error] 第 {i+1} 次重连失败")

        # 第二阶段：全部普通重连失败，物理关闭后重试
        print("[Warning] 普通重连全部失败，执行物理关闭...")

        self.physical_off()
        sleep(30)  # 等待30秒

        # 重新上电后，每20s检测重连，共计3次
        print(f"[Info] 尝试重新上电...")
        if self.physical_on():
            print(f"[OK] 激光器重新上电成功")
            return True

        for i in range(3):
            print(f"[Info] 等待 20 秒后检测重连... ({i+1}/3)")
            sleep(20)

            if self.connect():
                print(f"[OK] 重连成功！")
                self.set_wavelength(self.target_wavelength)
                return True
            else:
                print(f"[Error] 第 {i+1} 次重连失败")

        print("[Error] 激光器重连完全失败，需要人工干预")
        return False

    # ==================== 测试菜单 ====================

    def run_test_menu(self):
        """运行交互式测试菜单"""
        print("\n" + "=" * 60)
        print("激光器控制测试程序")
        print("=" * 60)
        print("确保激光器已连接并开启...")
        print()

        # 自动尝试连接
        if not self.is_connected():
            print("正在尝试连接激光器...")
            if not self.connect():
                print("[Error] 无法连接激光器，请检查地址和电源")
                return

        self.print_status()

        while True:
            print()
            print("-" * 60)
            print("1. 开启激光器（设置功率）")
            print("2. 关闭激光器（功率设为0，保持连接）")
            print("3. 设置功率为X mW")
            print("4. 读取当前状态")
            print("5. 测试断联重连功能")
            print("6. 物理关闭激光器（紧急）")
            print("7. 物理开启激光器（紧急关闭后）")
            print("8. 退出")
            print("-" * 60)

            try:
                choice = input("请输入选项 (1-8): ").strip()
                print()

                if choice == '1':
                    power = float(input("请输入功率 (mW): "))
                    self.set_power(power)

                elif choice == '2':
                    self.set_power(0)

                elif choice == '3':
                    power = float(input("请输入功率 (mW): "))
                    self.set_power(power)

                elif choice == '4':
                    self.print_status()

                elif choice == '5':
                    print("[Info] 模拟断联测试...")
                    print("[Info] 请手动断开激光器连接以测试重连功能")
                    input("按回车键开始等待断联检测...")
                    self.handle_disconnection()

                elif choice == '6':
                    confirm = input("确认物理关闭激光器？(y/n): ")
                    if confirm.lower() == 'y':
                        self.physical_off()

                elif choice == '7':
                    self.physical_on()

                elif choice == '8':
                    print("退出测试程序")
                    self.disconnect()
                    break

                else:
                    print("[Error] 无效选项")

            except KeyboardInterrupt:
                print("\n[Info] 用户中断")
                self.disconnect()
                break
            except Exception as e:
                print(f"[Error] 操作失败: {e}")


# ==================== 主程序 ====================

if __name__ == "__main__":
    controller = LaserController('TCPIP0::100.65.11.65::INSTR')
    controller.run_test_menu()
