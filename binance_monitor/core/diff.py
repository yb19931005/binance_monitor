from typing import List, Dict, Any, Tuple
from loguru import logger


def positions_to_map(positions: List[Dict[str, Any]]) -> Dict[str, Dict]:
    """以 symbol+side 作为唯一键"""
    result = {}
    for p in positions:
        key = f"{p['symbol']}|{p['side']}"
        result[key] = p
    return result


def diff_positions(
    old_map: Dict[str, Dict],
    new_map: Dict[str, Dict],
    min_notional: float = 0,
) -> Tuple[List[Dict], List[Dict], List[Dict]]:
    """
    返回: (opened, closed, changed)
    """
    opened, closed, changed = [], [], []

    old_keys = set(old_map.keys())
    new_keys = set(new_map.keys())

    # 新开仓
    for key in new_keys - old_keys:
        pos = new_map[key]
        if pos.get("notional", 0) >= min_notional:
            opened.append(pos)

    # 平仓
    for key in old_keys - new_keys:
        pos = old_map[key]
        if pos.get("notional", 0) >= min_notional:
            closed.append(pos)

    # 仓位变化（加仓/减仓）
    for key in old_keys & new_keys:
        old = old_map[key]
        new = new_map[key]
        old_amt = abs(old.get("position_amount", 0))
        new_amt = abs(new.get("position_amount", 0))

        # 数量变化超过 5% 或绝对变化较大时才认为有变化
        if abs(new_amt - old_amt) / max(old_amt, 1e-8) > 0.05 or abs(new_amt - old_amt) > 100:
            if new.get("notional", 0) >= min_notional or old.get("notional", 0) >= min_notional:
                changed.append({
                    "old": old,
                    "new": new,
                    "delta_amount": new_amt - old_amt,
                })

    return opened, closed, changed
