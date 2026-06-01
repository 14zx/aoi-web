"""Карта каналов RGB для WLED: палитра → байты в ``seg.col``."""

from __future__ import annotations

_PRESET_MAPS: dict[str, str] = {
    "rgb": "rgb",
    "swap_gb": "rbg",
    "brg": "brg",
}


def apply_color_map(rgb: tuple[int, int, int], color_map: str) -> tuple[int, int, int]:
    """Переставляет логические R,G,B в порядке слотов API (R, G, B)."""
    r, g, b = rgb
    slots = {"r": r, "g": g, "b": b}
    m = (color_map or "rgb").strip().lower()
    if len(m) != 3 or set(m) != {"r", "g", "b"}:
        return r, g, b
    return slots[m[0]], slots[m[1]], slots[m[2]]


def resolve_color_map(color_order: str, color_map: str | None = None) -> str:
    """Итоговая тройка ``rgb``/``brg``/… для ``apply_color_map``."""
    order = (color_order or "rgb").strip().lower()
    if order == "custom":
        m = (color_map or "rgb").strip().lower()
        if len(m) == 3 and set(m) == {"r", "g", "b"}:
            return m
        return "rgb"
    return _PRESET_MAPS.get(order, "rgb")


def color_map_from_calibration(observed: dict[str, str]) -> str:
    """Строит карту по наблюдениям: «отправил R из палитры → на ленте вижу …».

    ``observed['r']`` — цвет на ленте при чистом красном из палитры (``#FF0000``).
    Значения: ``r``, ``g`` или ``b``. Все три должны быть разными.
    """
    obs = {k: str(v).strip().lower() for k, v in observed.items() if k in "rgb"}
    if set(obs.keys()) != {"r", "g", "b"}:
        raise ValueError("Нужны наблюдения для r, g и b")
    vals = [obs[k] for k in "rgb"]
    if any(v not in "rgb" for v in vals):
        raise ValueError("Цвет на ленте: только r, g или b")
    if len(set(vals)) != 3:
        raise ValueError("На ленте должны быть три разных цвета (без повторов)")

    # Чистый канал палитры c идёт в слот API c; на ленте видно obs[c].
    # В этот слот кладём логический цвет obs[c], чтобы на ленте совпало с c.
    slot_idx = {"r": 0, "g": 1, "b": 2}
    out = ["r", "g", "b"]
    for logical in "rgb":
        out[slot_idx[logical]] = obs[logical]
    return "".join(out)
