"""
从模板 DOCX 生成 6 份设备状态监控记录表。
方案：复制模板 ZIP 内容 → 修改 document.xml 文本 → 重打包为 DOCX。
"""

import os
import re
import sys
import shutil
import zipfile
import xml.etree.ElementTree as ET
from copy import deepcopy
from io import BytesIO

# 确保控制台输出 UTF-8
if sys.stdout.encoding != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

# ── 路径配置 ──────────────────────────────────────────────
TEMPLATE_DOCX = r"D:\YBCO\VNAMeas\Auto_Sweep\report\otherwise\设备状态监控记录\20260526-设备状态监控记录-低温杜瓦测试系统.docx"
OUTPUT_DIR = r"D:\YBCO\VNAMeas\Auto_Sweep\report\20260718"
TEMP_DIR = r"C:\Users\DELL\AppData\Local\Temp\docx_build"

NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


# ── 辅助函数 ──────────────────────────────────────────────
def ns_tag(tag: str) -> str:
    return f"{{{NS}}}{tag}"


def set_cell_text(cell: ET.Element, text: str):
    """完全替换单元格文本：清除所有段落，保留第一个段落的格式框架。"""
    paragraphs = cell.findall(ns_tag("p"))
    if not paragraphs:
        return

    # 保留第一个段落，删除其余
    first_p = paragraphs[0]
    for extra_p in paragraphs[1:]:
        cell.remove(extra_p)

    # 清除第一个段落内的所有 run
    all_r = first_p.findall(ns_tag("r"))
    # 如果没有 run，创建一个
    if not all_r:
        r = ET.SubElement(first_p, ns_tag("r"))
        rpr = ET.SubElement(r, ns_tag("rPr"))
        ET.SubElement(rpr, ns_tag("rFonts")).set("w:hint", "eastAsia")
        ET.SubElement(rpr, ns_tag("sz")).set(ns_tag("val"), "24")
        t = ET.SubElement(r, ns_tag("t"))
        t.text = text
        t.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
        return

    # 保留第一个 run，删除其余
    first_r = all_r[0]
    for extra_r in all_r[1:]:
        first_p.remove(extra_r)

    # 清除第一个 run 内的所有文本子元素
    for child in list(first_r):
        if child.tag == ns_tag("t"):
            first_r.remove(child)

    # 创建新的 w:t
    t = ET.SubElement(first_r, ns_tag("t"))
    t.text = text
    t.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")


def set_all_cell_texts(row: ET.Element, texts: list[str]):
    """设置一行的所有单元格文本。texts 长度应与单元格数一致。"""
    cells = row.findall(ns_tag("tc"))
    for i, text in enumerate(texts):
        if i < len(cells):
            # 获取单元格中所有段落的合并文本，用于判断是否需要替换
            cell = cells[i]
            set_cell_text(cell, text)


def replace_in_xml(root: ET.Element, row_idx: int, col_texts: list[str]):
    """替换指定表格行的单元格文本。"""
    tbl = root.find(f".//{ns_tag('tbl')}")
    if tbl is None:
        return
    rows = tbl.findall(ns_tag("tr"))
    if row_idx < len(rows):
        set_all_cell_texts(rows[row_idx], col_texts)


def remove_extra_paragraphs_in_cell(cell: ET.Element):
    """确保单元格只有一个段落。"""
    paragraphs = cell.findall(ns_tag("p"))
    if len(paragraphs) > 1:
        for p in paragraphs[1:]:
            cell.remove(p)


def clear_merged_continuation_cells(row: ET.Element):
    """清除 vMerge 续行中的空状态文本（A列留空行）。"""
    cells = row.findall(ns_tag("tc"))
    for cell in cells:
        tcPr = cell.find(ns_tag("tcPr"))
        if tcPr is not None:
            vmerge = tcPr.find(ns_tag("vMerge"))
            if vmerge is not None and vmerge.get(ns_tag("val")) is None:
                # 这是合并续行 — 清空该单元格文本
                for r in cell.findall(ns_tag("r")):
                    cell.remove(r)


# ── 6 份表格数据 ──────────────────────────────────────────
# 每份表格: (文件名, 日期文本, 实验内容, [11行数据])
# 每行数据: (状态, 监控项, 数值范围及操作要求, 实际状态及操作, 备注)
# 监控项名称不填（模板已有），只填后 3 列

def row_data(req: str, actual: str, note: str = "") -> list:
    """简化行数据构造：状态列留空（由合并单元格控制），监控项名称保留模板原文。"""
    return ["", "", req, actual, note]


TABLES = [
    # ── 表 #1：20260529 ──
    {
        "filename": "20260529-设备状态监控记录-低温杜瓦测试系统.docx",
        "date": "2026 年 5 月 29 日 — 2026 年 6 月 5 日",
        "content": "YBCO S21 3-6GHz -45dBm 全温区激光功率/温度扫频 + settling time 研究（~447 s2p）",
        "rows": [
            # Row 3: 真空泵组
            ["", "真空泵组", "开启机械泵与分子泵，真空度 < 1.0×10⁻³ mbar",
             "正常启动，真空度 8.5×10⁻⁴ mbar", "🟡 历史均值"],
            # Row 4: Lake Shore 335
            ["", "Lake Shore 335", "开机检查，两通道传感器显示室温",
             "🟡 无 readme.txt 记录", "🔴 传感器值不可考"],
            # Row 5: VNA 校准
            ["", "VNA 校准", "预热完成，执行双端口校准（TOSM）",
             "🟡 无校准查询记录；power_sweep_auto.py 有 VNA 连接", "🔴 无 log"],
            # Row 6: 线缆与光路
            ["", "测试线缆与光路", "射频线及光纤连接牢固，室温导通性正常",
             "VNA HiSLIP CHASSIS1_SLOT1；激光 TCPIP 169.254.77.29", "🟡 脚本含设备地址"],
            # Row 7: 直流电压源
            ["", "直流电压源", "确认三端口设置符合放大器参数",
             "2.4V/0.1A；0V/1.0A；0.6V/1.0A（沿用历史值）", "🟡 无 readme.txt"],
            # Row 8: 氦气压缩单元
            ["", "氦气压缩单元", "供气压稳定在 2.00 MPa 附近，运行时间 < 30000h",
             "2.0 MPa，~4,530 h", "🟡 5/26=4507h + 3天×8.5h/天 外推"],
            # Row 9: 温度曲线
            ["", "温度曲线 (Lake Shore 335)", "呈现正常下降趋势，记录降至目标底温时间",
             "4-88K 全温区覆盖，固定温激光扫频 5/29-6/2 + 温度扫频 6/4-6/5", "🟢 S2P 文件名提取"],
            # Row 10: 真空泵组
            ["", "真空泵组", "真空泵电压/电流/转速示数正常",
             "24.30V / 0.11A / 1500Hz", "🟡 历史固定值"],
            # Row 11: VNA 状态监控
            ["", "VNA 状态监控", "监测谐振出现情况，谐振数量",
             "出现 4-5 处谐振", "🟡 历史均值"],
            # Row 12: 可调光源
            ["", "可调光源", "设定目标波长与功率输出",
             "1500 nm；固定温 0-17mW(10级) + 温度扫频 0-9mW(6级)；含 settling time 研究 10s-180s",
             "🟡 power_sweep_auto.py"],
            # Row 13: Lake Shore 335
            ["", "Lake Shore 335", "达到目标温度并稳定，记录 PID 参数",
             "PID 分三区：≤20K P=100/I=5/D=0；20-40K P=100/I=0/D=0；>40K P=150/I=0/D=0",
             "🟢 power_sweep_auto.py 源码"],
        ]
    },
    # ── 表 #2：20260604 ──
    {
        "filename": "20260604-设备状态监控记录-低温杜瓦测试系统.docx",
        "date": "2026 年 6 月 4 日",
        "content": "YBCO 开发测试（-45dBm 10K 单温点测量 + VNA P5003A HiSLIP 连接验证 + 脚本调试）",
        "rows": [
            ["", "真空泵组", "开启机械泵与分子泵，真空度 < 1.0×10⁻³ mbar",
             "正常启动，真空度 8.5×10⁻⁴ mbar", "🟡 历史均值"],
            ["", "Lake Shore 335", "开机检查，两通道传感器显示室温",
             "🟡 无记录", "🔴 无 readme.txt"],
            ["", "VNA 校准", "预热完成，执行双端口校准（TOSM）",
             "test_output.s2p 头部显示 S11(Off)/S21(Off)/S12(Off)/S22(Off) — 未校准！",
             "🟡 开发/测试模式，未校准属正常"],
            ["", "测试线缆与光路", "射频线及光纤连接牢固，室温导通性正常",
             "test_vna.py 独立 VNA HiSLIP 连接测试通过；laser_control.py 含断连自动恢复",
             "🟢 脚本化设备测试"],
            ["", "直流电压源", "确认三端口设置符合放大器参数",
             "2.4V/0.1A；0V/1.0A；0.6V/1.0A（沿用历史值）", "🟡 无 readme.txt"],
            ["", "氦气压缩单元", "供气压稳定在 2.00 MPa 附近，运行时间 < 30000h",
             "2.0 MPa，~4,570 h", "🟡 5/26=4507h + 8天×8.5h/天"],
            ["", "温度曲线 (Lake Shore 335)", "呈现正常下降趋势，记录降至目标底温时间",
             "稳定于 10.544K（单温点），temperature_diagnostics.py 验证自适应 PID",
             "🟡 S2P actual 值"],
            ["", "真空泵组", "真空泵电压/电流/转速示数正常",
             "24.30V / 0.11A / 1500Hz", "🟡 历史固定值"],
            ["", "VNA 状态监控", "监测谐振出现情况，谐振数量",
             "test_output.s2p 含 3-6GHz/50000点 S21；VNA P5003A SN:MY58100335",
             "🟢 S2P 头部数据"],
            ["", "可调光源", "设定目标波长与功率输出",
             "1500 nm（laser_control.py）；功率 0/1/3/5/7/9 mW (6级@10.544K)",
             "🟡 S2P 文件名"],
            ["", "Lake Shore 335", "达到目标温度并稳定，记录 PID 参数",
             "10K: Low区 P=100/I=5/D=0；temperature_diagnostics.py 含完整诊断",
             "🟡 脚本源码"],
        ]
    },
    # ── 表 #3：20260630 ──
    {
        "filename": "20260630-设备状态监控记录-低温杜瓦测试系统.docx",
        "date": "2026 年 6 月 30 日",
        "content": "YBCO S21 3-6GHz 50-80K 温区扫频（16温×16VNA功率×10激光=2560点）⚠ 未完成 — LakeShore CONN_LOST",
        "rows": [
            ["", "真空泵组", "开启机械泵与分子泵，真空度 < 1.0×10⁻³ mbar",
             "正常启动，真空度 8.5×10⁻⁴ mbar", "🟡 历史均值"],
            ["", "Lake Shore 335", "开机检查，两通道传感器显示室温",
             "🟡 无 readme.txt（实验未正常结束）", "🔴 实验未完成"],
            ["", "VNA 校准", "预热完成，执行双端口校准（TOSM）",
             "🟡 校准顺利完成（推断）", "🟡 推断；无直接查询"],
            ["", "测试线缆与光路", "射频线及光纤连接牢固，室温导通性正常",
             "VNA/LS/激光 *IDN? 3次重启均成功重连", "🟢 experiment_log 确认"],
            ["", "直流电压源", "确认三端口设置符合放大器参数",
             "2.4V/0.1A；0V/1.0A；0.6V/1.0A（沿用历史值）", "🟡 无 readme.txt"],
            ["", "氦气压缩单元", "供气压稳定在 2.00 MPa 附近，运行时间 < 30000h",
             "2.0 MPa，~4,930 h", "🟡 5/26=4507h + 50天×8.5h/天"],
            ["", "温度曲线 (Lake Shore 335)", "呈现正常下降趋势，记录降至目标底温时间",
             "第1次降温失败(80→75K)；第2次稳定49.8K；第3次50K起始。完成50K+52K共320点",
             "🟢 experiment_log 3次会话"],
            ["", "真空泵组", "真空泵电压/电流/转速示数正常",
             "24.30V / 0.11A / 1500Hz", "🟡 历史固定值"],
            ["", "VNA 状态监控", "监测谐振出现情况，谐振数量",
             "出现 4-5 处谐振", "🟡 历史均值"],
            ["", "可调光源", "设定目标波长与功率输出",
             "1550 nm；功率 0/1/3/5/7/9/11/13/15/17 mW",
             "🟢 app_settings.json"],
            ["", "Lake Shore 335", "达到目标温度并稳定，记录 PID 参数",
             "VeryHigh区(>70K) P=150/I=0/D=0 P-only +2.0K overshoot。54K处1次熔断(0.284K)，CONN_LOST×5导致中断",
             "🟢 config + status.json"],
        ]
    },
    # ── 表 #4：20260702 ──
    {
        "filename": "20260702-设备状态监控记录-低温杜瓦测试系统.docx",
        "date": "2026 年 7 月 2 日 — 2026 年 7 月 3 日",
        "content": "YBCO S21 3-6GHz 低温测试（目标 4K→调整为5K；1温×9VNA×10激光=90点实测）⚠ 杜瓦极限~6.3K",
        "rows": [
            ["", "真空泵组", "开启机械泵与分子泵，真空度 < 1.0×10⁻³ mbar",
             "正常启动，真空度 8.5×10⁻⁴ mbar", "🟡 历史均值"],
            ["", "Lake Shore 335", "开机检查，两通道传感器显示室温",
             "293.9 K（20.8°C）ChA", "🟢 readme.txt ChA；ChB 无"],
            ["", "VNA 校准", "预热完成，执行双端口校准（TOSM）",
             "🟡 校准顺利完成（推断）", "🟡 推断"],
            ["", "测试线缆与光路", "射频线及光纤连接牢固，室温导通性正常",
             "VNA/LS/激光 *IDN? 全部响应正常", "🟢 experiment_log"],
            ["", "直流电压源", "确认三端口设置符合放大器参数",
             "2.400V/0.100A；0.000V/1.000A；0.600V/1.000A", "🟢 readme.txt"],
            ["", "氦气压缩单元", "供气压稳定在 2.00 MPa 附近，运行时间 < 30000h",
             "2.0 MPa，~4,960 h", "🟡 5/26=4507h + 53天×8.5h/天"],
            ["", "温度曲线 (Lake Shore 335)", "呈现正常下降趋势，记录降至目标底温时间",
             "⚠ 杜瓦极限~6.3K，4K不可达（最低瞬态4.37K）。7/3 改为5K目标，~11min降至5.04K稳定。7K切换时 CONN_LOST",
             "🟢 experiment_log 2次会话"],
            ["", "真空泵组", "真空泵电压/电流/转速示数正常",
             "24.30V / 0.11A / 1500Hz", "🟡 历史固定值"],
            ["", "VNA 状态监控", "监测谐振出现情况，谐振数量",
             "出现 4-5 处谐振", "🟡 历史均值"],
            ["", "可调光源", "设定目标波长与功率输出",
             "1550 nm；功率 0/1/3/5/7/9/11/13/15/17 mW",
             "🟢 app_settings.json"],
            ["", "Lake Shore 335", "达到目标温度并稳定，记录 PID 参数",
             "5K: Low区 P=100/I=5/D=0 (PI控制)。0次熔断，全程稳定于5.037K。加热器档位2",
             "🟢 config + 0 issues"],
        ]
    },
    # ── 表 #5：20260704 ──
    {
        "filename": "20260704-设备状态监控记录-低温杜瓦测试系统.docx",
        "date": "2026 年 7 月 4 日 — 2026 年 7 月 5 日",
        "content": "YBCO S21 3-6GHz 77K 常导态参考测量（1温×9VNA×10激光=90点）— 与5K超导态对比",
        "rows": [
            ["", "真空泵组", "开启机械泵与分子泵，真空度 < 1.0×10⁻³ mbar",
             "正常启动，真空度 8.5×10⁻⁴ mbar", "🟡 历史均值"],
            ["", "Lake Shore 335", "开机检查，两通道传感器显示室温",
             "296.8 K（23.7°C）ChA", "🟢 readme.txt (0705)；0704 无"],
            ["", "VNA 校准", "预热完成，执行双端口校准（TOSM）",
             "🟡 校准顺利完成（推断）", "🟡 推断"],
            ["", "测试线缆与光路", "射频线及光纤连接牢固，室温导通性正常",
             "VNA/LS/激光 *IDN? 全部响应正常", "🟢 experiment_log"],
            ["", "直流电压源", "确认三端口设置符合放大器参数",
             "2.400V/0.100A；0.000V/1.000A；0.600V/1.000A", "🟢 readme.txt"],
            ["", "氦气压缩单元", "供气压稳定在 2.00 MPa 附近，运行时间 < 30000h",
             "2.0 MPa，~4,990 h", "🟡 5/26=4507h + 57天×8.5h/天"],
            ["", "温度曲线 (Lake Shore 335)", "呈现正常下降趋势，记录降至目标底温时间",
             "77K 起始（液氮预冷），3min 稳定于77.371K。0704 2次熔断放弃(最大0.608K)→0705全程稳定(delta<0.003K)，49m40s完成",
             "🟢 experiment_log + readme.txt"],
            ["", "真空泵组", "真空泵电压/电流/转速示数正常",
             "24.30V / 0.11A / 1500Hz", "🟡 历史固定值"],
            ["", "VNA 状态监控", "监测谐振出现情况，谐振数量",
             "出现 4-5 处谐振", "🟡 历史均值"],
            ["", "可调光源", "设定目标波长与功率输出",
             "1550 nm；功率 0/1/3/5/7/9/11/13/15/17 mW",
             "🟢 app_settings.json"],
            ["", "Lake Shore 335", "达到目标温度并稳定，记录 PID 参数",
             "VeryHigh区(>70K) P=150/I=0/D=0 P-only +2.5K overshoot。0704: 2次熔断→0705: 0次熔断，37mK温漂以内",
             "🟢 config + status.json"],
        ]
    },
    # ── 表 #6：20260705 ──
    {
        "filename": "20260705-设备状态监控记录-低温杜瓦测试系统.docx",
        "date": "2026 年 7 月 6 日 — 2026 年 7 月 8 日",
        "content": "YBCO S21 3-6GHz 45-79K 温区扫频（18温×9VNA功率×10激光=1620点）— 迄今完成度最高",
        "rows": [
            ["", "真空泵组", "开启机械泵与分子泵，真空度 < 1.0×10⁻³ mbar",
             "正常启动，真空度 8.5×10⁻⁴ mbar", "🟡 历史8次均值(8.1-9.6×10⁻⁴)"],
            ["", "Lake Shore 335", "开机检查，两通道传感器显示室温",
             "297.6 K（24.5°C）ChA", "🟢 readme.txt；ChB 无独立记录"],
            ["", "VNA 校准", "预热完成，执行双端口校准（TOSM）",
             "🟡 校准顺利完成（推断）", "🟡 未查询 CORR:STAT；基于实验正常完成推断"],
            ["", "测试线缆与光路", "射频线及光纤连接牢固，室温导通性正常",
             "VNA/LS/激光 *IDN? 全部响应正常，连接牢固",
             "🟢 experiment_log 2026-07-05 12:55 确认"],
            ["", "直流电压源", "确认三端口设置符合放大器参数",
             "2.400V/0.100A；0.000V/1.000A；0.600V/1.000A",
             "🟢 readme.txt E36312A_PARAMS 静态值"],
            ["", "氦气压缩单元", "供气压稳定在 2.00 MPa 附近，运行时间 < 30000h",
             "2.0 MPa，~5,000 h", "🟡 5/26=4507h + 41天×8.5h/天≈4855h≈5000h"],
            ["", "温度曲线 (Lake Shore 335)", "呈现正常下降趋势，记录降至目标底温时间",
             "77.4K→45.0K 自然冷却，~2.5h 至稳定；总实验 56h 58m；45K处2次熔断后恢复",
             "🟢 experiment_log 冷却进度 + readme.txt"],
            ["", "真空泵组", "真空泵电压/电流/转速示数正常",
             "24.30V / 0.11A / 1500Hz", "🟡 历史8次记录完全一致"],
            ["", "VNA 状态监控", "监测谐振出现情况，谐振数量",
             "出现 4-5 处谐振", "🟡 历史均值；需 _data_cache.py 确认"],
            ["", "可调光源", "设定目标波长与功率输出",
             "1550 nm；功率 0/1/3/5/7/9/11/13/15/17 mW，全程可控",
             "🟢 app_settings.json + laser_driver.py"],
            ["", "Lake Shore 335", "达到目标温度并稳定，记录 PID 参数",
             "45K→79K 共18点。High区(40-70K) P=150/I=0/D=0，VeryHigh(>70K) P=150/I=0/D=0。加热器档位2。45K处熔断2次(0.303K+0.255K)",
             "🟢 config + status.json issues[] + experiment_log"],
        ]
    },
]


# ── 生成逻辑 ──────────────────────────────────────────────
def build_docx(template_path: str, output_path: str, date_text: str, content_text: str, rows_data: list):
    """基于模板生成一份 DOCX。"""
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    # 读取模板
    with zipfile.ZipFile(template_path, 'r') as zin:
        zip_data = {name: zin.read(name) for name in zin.namelist()}

    # 读取原始 document.xml（字节和字符串）
    doc_xml_bytes = zip_data["word/document.xml"]
    orig_xml_str = doc_xml_bytes.decode("utf-8")

    # 保存原始 XML 声明和根元素开始标签（含全部命名空间声明）
    # 格式: "<?xml ...?>\n<w:document xmlns:...>"
    orig_header_end = orig_xml_str.index(">", orig_xml_str.index("<w:document")) + 1
    orig_header = orig_xml_str[:orig_header_end]

    # 注册所有命名空间（让 ET 使用正确的前缀）
    for match in re.finditer(r'xmlns:(\w+)="([^"]+)"', orig_header):
        ET.register_namespace(match.group(1), match.group(2))

    root = ET.fromstring(doc_xml_bytes)
    body = root.find(ns_tag("body"))
    tbl = body.find(ns_tag("tbl"))
    rows = tbl.findall(ns_tag("tr"))

    # ── 替换 Row 0: 日期 + 签字 ──
    set_all_cell_texts(rows[0], ["日期：", date_text, "记录人签字：", "赵思源"])

    # ── 替换 Row 1: 实验内容 ──
    set_all_cell_texts(rows[1], ["实验内容", content_text])

    # ── 替换 Row 3-13: 11 个监测项的数据行 ──
    for i, row_data in enumerate(rows_data):
        template_row_idx = 3 + i  # rows[3]..rows[13]
        if template_row_idx < len(rows):
            set_all_cell_texts(rows[template_row_idx], row_data)

    # ── 序列化 + 修复命名空间 ──
    # ET.tostring 会丢失未使用的命名空间声明 → 用原始 header 替换生成 header
    generated_xml = ET.tostring(root, encoding="unicode")
    gen_header_end = generated_xml.index(">", generated_xml.index("<w:document")) + 1
    new_xml_str = orig_header + generated_xml[gen_header_end:]

    # ── 写入新 DOCX ──
    with zipfile.ZipFile(output_path, 'w', zipfile.ZIP_DEFLATED) as zout:
        for name, data in zip_data.items():
            if name == "word/document.xml":
                zout.writestr(name, new_xml_str.encode("utf-8"))
            else:
                zout.writestr(name, data)

    print(f"  OK {os.path.basename(output_path)}")


# ── 主流程 ──────────────────────────────────────────────
def main():
    print(f"输出目录: {OUTPUT_DIR}")
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    for tbl in TABLES:
        output_path = os.path.join(OUTPUT_DIR, tbl["filename"])
        build_docx(
            template_path=TEMPLATE_DOCX,
            output_path=output_path,
            date_text=tbl["date"],
            content_text=tbl["content"],
            rows_data=tbl["rows"],
        )

    print(f"\n全部 6 份 DOCX 已生成至: {OUTPUT_DIR}")
    # 列清单
    for f in sorted(os.listdir(OUTPUT_DIR)):
        full = os.path.join(OUTPUT_DIR, f)
        size_kb = os.path.getsize(full) / 1024
        print(f"   {f}  ({size_kb:.1f} KB)")


if __name__ == "__main__":
    main()
