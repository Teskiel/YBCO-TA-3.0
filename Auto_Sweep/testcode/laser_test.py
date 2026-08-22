# -*- coding: utf-8 -*-
"""
Interactive laser test menu (standalone debugging tool).

Uses LaserController from laser_driver.
Run: python testcode/laser_test.py
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from laser_driver import LaserController


def main():
    print("\n" + "=" * 60)
    print("Laser Control Test Program")
    print("=" * 60)
    print("Make sure the laser is connected and powered on...")
    print()

    controller = LaserController("TCPIP0::100.65.11.65::INSTR")

    if not controller.is_connected():
        print("Attempting to connect to laser...")
        if not controller.connect():
            print("[Error] Cannot connect to laser. Check address and power.")
            return

    controller.print_status()

    while True:
        print()
        print("-" * 60)
        print("1. Turn laser ON (set power)")
        print("2. Turn laser OFF (power=0, keep connection)")
        print("3. Set power to X mW")
        print("4. Read current status")
        print("5. Test disconnection recovery")
        print("6. Physical OFF (emergency)")
        print("7. Physical ON (after emergency off)")
        print("8. Exit")
        print("-" * 60)

        try:
            choice = input("Select (1-8): ").strip()
            print()

            if choice == "1":
                power = float(input("Power (mW): "))
                controller.set_power(power)

            elif choice == "2":
                controller.set_power(0)

            elif choice == "3":
                power = float(input("Power (mW): "))
                controller.set_power(power)

            elif choice == "4":
                controller.print_status()

            elif choice == "5":
                print("[Info] Simulating disconnection test...")
                print("[Info] Manually disconnect the laser to test recovery")
                input("Press Enter to begin waiting for disconnection...")
                controller.handle_disconnection()

            elif choice == "6":
                confirm = input("Confirm physical shutdown? (y/n): ")
                if confirm.lower() == "y":
                    controller.physical_off()

            elif choice == "7":
                controller.physical_on()

            elif choice == "8":
                print("Exiting test program")
                controller.disconnect()
                break

            else:
                print("[Error] Invalid option")

        except KeyboardInterrupt:
            print("\n[Info] User interrupt")
            controller.disconnect()
            break
        except Exception as e:
            print(f"[Error] Operation failed: {e}")


if __name__ == "__main__":
    main()
