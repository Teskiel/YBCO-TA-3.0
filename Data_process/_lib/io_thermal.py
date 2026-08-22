# _lib/io_thermal.py — 芯片元数据 / 标定模型的文件存取
"""chip.json、run.json 与 thermal_models/*.json 的读写。

约定
----
数据目录组织（在 Auto_Sweep 之上新增两级母文件夹）：
    data/YBCO#1145__t50nm__PLD/           芯片层：chip.json
        run03__20260609__cooldown2/       测量层：run.json
            6K/ 10K/ ... 77K/             现有结构不变

文件夹名是给人看的，机器可读字段一律走 sidecar JSON（不解析文件夹自由文本）。
Tc 存在 chip.json 的 tc_k 字段，是模型的权威来源（用户实测优先，自由拟合备用）。

历史扁平数据集（如 20260609-0624__6-80K__full，直接是 {temp}K/ 顶层）兼容：
把 chip.json 直接放进数据集目录，或通过 --chip-json 外部指定。

本模块仅用 json + pathlib（标准库），不含拟合逻辑（见 thermal_model.py）。
"""
import json
from pathlib import Path

CHIP_FILENAME = "chip.json"
RUN_FILENAME = "run.json"

# 标定存档目录：Data_process/thermal_models/{chip_id}__{run_id}.json
THERMAL_MODELS_DIR = Path(__file__).resolve().parent.parent / "thermal_models"

# resposition 归档目录名（位于芯片层下）
RESPOSITION_DIRNAME = "resposition"


# =========================================================================
# 芯片 / 测量元数据
# =========================================================================

def find_chip_json(data_dir):
    """在数据目录树中查找 chip.json，返回其路径或 None。

    查找顺序：
      1. data_dir/chip.json                              （扁平数据集，手工放入）
      2. 自下而上两级：parent、parent.parent              （新布局 run 层 → 芯片层）
      3. 向下探测直接子目录：{chip}/chip.json             （从芯片层传入时）
    向下探测是为两级布局：从芯片层传入时 run.json 在下方，chip.json 却就在
    本层；而 run 层传入时自下而上已能命中。
    """
    d = Path(data_dir)
    for cand in (d, d.parent, d.parent.parent):
        p = cand / CHIP_FILENAME
        if p.is_file():
            return p
    # 向下探测：本层是芯片层（子目录里是 run），chip.json 应在本层或某个子目录
    for child in sorted(d.iterdir()) if d.is_dir() else []:
        if child.is_dir():
            p = child / CHIP_FILENAME
            if p.is_file():
                return p
    return None


def resolve_chip_dir(data_dir):
    """从数据目录推导芯片层目录（chip.json 的父目录），失败返回 None。

    扁平数据集（chip.json 在数据目录内）返回数据目录自身；两级布局返回芯片层。
    """
    p = find_chip_json(data_dir)
    return Path(p).parent if p else None


def find_run_json(data_dir):
    """在数据目录树中查找 run.json，返回路径或 None。"""
    d = Path(data_dir)
    for cand in (d, d.parent, d.parent.parent):
        p = cand / RUN_FILENAME
        if p.is_file():
            return p
    return None


def load_chip_json(data_dir=None, path=None):
    """加载 chip.json，返回 dict 或 None（找不到时）。

    path 显式指定时优先；否则在 data_dir 树中查找。
    """
    p = Path(path) if path else (find_chip_json(data_dir) if data_dir else None)
    if p is None or not Path(p).is_file():
        return None
    with open(p, "r", encoding="utf-8") as f:
        return json.load(f)


def load_run_json(data_dir=None, path=None):
    """加载 run.json，返回 dict 或 None。"""
    p = Path(path) if path else (find_run_json(data_dir) if data_dir else None)
    if p is None or not Path(p).is_file():
        return None
    with open(p, "r", encoding="utf-8") as f:
        return json.load(f)


def resolve_tc_k(chip, tc_k=None, tc_free=False):
    """解析拟合用的 Tc。

    规则：tc_k 显式传入 > 0 → 用它（来源 "config"）。
          否则 chip.json 的 tc_k → 用它（来源 "chip.json"）。
          tc_free=True 时调用方可忽略返回值，仅作初始值/文档用。
    返回 (tc_k_float|None, source_str)。
    """
    if tc_k is not None and tc_k > 0:
        return float(tc_k), "config"
    if chip and chip.get("tc_k"):
        return float(chip["tc_k"]), "chip.json"
    return None, "missing"


def write_chip_json(path, data):
    """写 chip.json（供新数据集初始化用）。"""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


# =========================================================================
# 标定存档
# =========================================================================

def calibration_path(chip_id, run_id):
    """thermal_models/{chip_id}__{run_id}.json 的绝对路径。"""
    return THERMAL_MODELS_DIR / f"{chip_id}__{run_id}.json"


def save_calibration(fit_dict, chip_id, run_id, source_data_dir,
                     temps_k=None, freqs_hz=None, backtest=None):
    """把拟合结果归档到 thermal_models/。

    Parameters
    ----------
    fit_dict : thermal_model.to_dict() 的输出
    chip_id, run_id : str
    source_data_dir : str|Path   数据来源（记录用）
    temps_k, freqs_hz : 可选，参与拟合的 (T_actual, f) 点，方便日后复验
    backtest : 可选，backtest_walk_forward() 返回的 dict（只存标量指标）

    Returns
    -------
    Path  写入的文件路径
    """
    doc = {
        "schema_version": 1,
        "chip_id": chip_id,
        "run_id": run_id,
        "source_data_dir": str(source_data_dir),
        "fit": fit_dict,
    }
    if temps_k is not None and freqs_hz is not None:
        import numpy as np
        temps_k = np.asarray(temps_k, float)
        freqs_hz = np.asarray(freqs_hz, float)
        doc["data"] = {
            "temps_k": np.round(temps_k, 4).tolist(),
            "freqs_hz": np.round(freqs_hz, 2).tolist(),
        }
    if backtest is not None:
        doc["backtest"] = {
            "max_abs_delta_hz": float(backtest.get("max_abs_delta_hz", np.nan)),
            "coverage": float(backtest.get("coverage", np.nan)),
        }
    path = calibration_path(chip_id, run_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=2, ensure_ascii=False, default=str)
    return path


def load_calibration(chip_id=None, run_id=None, path=None):
    """加载标定存档，返回 dict 或 None。

    三个参数给任一即可：path 直接指定；chip_id+run_id 推导路径；
    只给 chip_id 时返回该芯片的**最新**一份（按修改时间）。
    """
    if path is not None:
        p = Path(path)
    elif chip_id and run_id:
        p = calibration_path(chip_id, run_id)
    elif chip_id:
        cands = sorted(THERMAL_MODELS_DIR.glob(f"{chip_id}__*.json"),
                       key=lambda p: p.stat().st_mtime, reverse=True)
        p = cands[0] if cands else None
    else:
        return None
    if p is None or not Path(p).is_file():
        return None
    with open(p, "r", encoding="utf-8") as f:
        return json.load(f)


def list_calibrations():
    """列出 thermal_models/ 下所有存档文件路径。"""
    return sorted(THERMAL_MODELS_DIR.glob("*.json"))


# =========================================================================
# resposition 归档（芯片旁工作副本，txt 内是 JSON）
# =========================================================================

def _res_laser_tag(laser_mw):
    """激光功率后缀：0mW 无后缀（基准），否则 '&{PP}mW' 零填充两位。"""
    mw = int(laser_mw)
    return "" if mw == 0 else f"&{mw:02d}mW"


def resposition_dir(chip_dir):
    """{chip}/resposition/ 的绝对路径（不建目录，仅推导）。"""
    return Path(chip_dir) / RESPOSITION_DIRNAME


def res_record_path(chip_dir, run_id, laser_mw=0, version=None):
    """某 (run, laser) 的谐振记录文件路径。

    version 为 None 时返回基名（无 __v 后缀）；>=2 时加 '__v{M}'。
    """
    base = f"res__run{run_id}{_res_laser_tag(laser_mw)}"
    if version is not None and int(version) >= 2:
        base += f"__v{int(version)}"
    return resposition_dir(chip_dir) / f"{base}.txt"


def list_res_versions(chip_dir, run_id, laser_mw=0):
    """列出某 (run, laser) 的所有版本号（升序），无记录时返回 []。"""
    base = f"res__run{run_id}{_res_laser_tag(laser_mw)}"
    versions = []
    d = resposition_dir(chip_dir)
    if not d.is_dir():
        return versions
    for p in d.glob(f"{base}*.txt"):
        name = p.stem
        if name == base:
            versions.append(1)
        elif name.startswith(base + "__v"):
            try:
                versions.append(int(name.rsplit("__v", 1)[1]))
            except ValueError:
                pass
    return sorted(set(versions))


def _write_txt(path, doc):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=2, ensure_ascii=False, default=str)


def _read_txt(path):
    if not Path(path).is_file():
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_res_record(chip_dir, run_id, laser_mw, record, version=None):
    """写入一份谐振记录，返回 (path, version)。

    version 为 None 时：若已有记录则自动取"最高版本 + 1"（版本化并存，最新为准），
    否则写基名（v1）。显式传 version 则按给定版本写。
    """
    if version is None:
        existing = list_res_versions(chip_dir, run_id, laser_mw)
        version = max(existing) + 1 if existing else 1
    version = int(version)
    doc = dict(record)
    doc.setdefault("run_id", str(run_id))
    doc.setdefault("laser_mw", int(laser_mw))
    doc["version"] = version
    path = res_record_path(chip_dir, run_id, laser_mw, version=version)
    _write_txt(path, doc)
    return path, version


def load_res_record(chip_dir, run_id, laser_mw=0, latest=True, version=None):
    """读取某 (run, laser) 的谐振记录。

    latest=True 时取最高版本；否则按 version 指定（默认基名 v1）。
    """
    if version is None and not latest:
        version = 1
    if latest and version is None:
        versions = list_res_versions(chip_dir, run_id, laser_mw)
        version = max(versions) if versions else 1
    return _read_txt(res_record_path(chip_dir, run_id, laser_mw, version=version))


def fit_txt_path(chip_dir, chip_id):
    """fit__{chip_id}.txt 的路径（拟合函数，温度+激光，单一文件被覆盖更新）。"""
    return resposition_dir(chip_dir) / f"fit__{chip_id}.txt"


def save_fit_txt(chip_dir, chip_id, fit_dict):
    """把拟合函数写入 fit__{chip_id}.txt（覆盖更新）。"""
    path = fit_txt_path(chip_dir, chip_id)
    doc = dict(fit_dict)
    doc.setdefault("chip_id", chip_id)
    _write_txt(path, doc)
    return path


def load_fit_txt(chip_dir, chip_id):
    """读取 fit__{chip_id}.txt，不存在返回 None。"""
    return _read_txt(fit_txt_path(chip_dir, chip_id))
