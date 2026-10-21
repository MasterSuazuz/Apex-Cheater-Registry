"""Кто сейчас в эфире: устойчивое определение наблюдаемого игрока по сканам экрана.

Ключ игрока — идентификатор с экрана наблюдения, без него — ник. Смена игрока принимается:
- сразу, если ID прочитан и ник на плашке совпал со строкой отряда (два независимых источника);
- иначе — когда одно и то же прочитано в двух сканах подряд (одиночная ошибка OCR не переключит досье).
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from . import legends
from .screenreader import ScanResult, same_name


@dataclass
class Player:
    name: str = ""
    player_id: str = ""
    legend: str = ""
    clan_tag: str = ""
    teammates: list = field(default_factory=list)
    portrait: np.ndarray | None = None
    since: float = 0.0          # секунда записи, с которой на экране этот игрок

    @property
    def key(self) -> str:
        return self.player_id or self.name.casefold()


def _key(scan: ScanResult) -> str:
    return scan.player_id or scan.name.casefold()


class PlayerTracker:
    def __init__(self):
        self.current: Player | None = None
        self._candidate: tuple[str, int] = ("", 0)

    def reset(self) -> None:
        self.current = None
        self._candidate = ("", 0)

    def same_player(self, scan: ScanResult) -> bool:
        c = self.current
        if c is None or not (scan.player_id or scan.name):
            return False
        if scan.player_id and c.player_id:
            return scan.player_id == c.player_id
        return same_name(scan.name, c.name)

    def push(self, scan: ScanResult, t: float) -> Player | None:
        """Возвращает нового игрока, когда наблюдаемый сменился; иначе None (и уточняет текущего)."""
        if not scan.spectating or not (scan.player_id or scan.name):
            return None
        if self.same_player(scan):
            self._refine(scan)
            self._candidate = ("", 0)
            return None
        key = _key(scan)
        prev_key, count = self._candidate
        count = count + 1 if prev_key == key else 1
        self._candidate = (key, count)
        if (scan.player_id and scan.confident) or count >= 2:
            self.current = Player(scan.name, scan.player_id, clan_tag=scan.clan_tag,
                                  teammates=list(scan.teammates), portrait=scan.portrait, since=t)
            self.current.legend = legends.match(scan.portrait)[0]
            self._candidate = ("", 0)
            return self.current
        return None

    def _refine(self, scan: ScanResult) -> None:
        """Тот же игрок: дополняем то, чего не хватало (ID, ник с плашки, портрет, легенда)."""
        c = self.current
        if scan.player_id and not c.player_id:
            c.player_id = scan.player_id
        if scan.confident and scan.name and scan.name != c.name:
            c.name = scan.name
        if scan.clan_tag:
            c.clan_tag = scan.clan_tag
        for m in scan.teammates:
            if m not in c.teammates:
                c.teammates.append(m)
        if scan.portrait is not None:
            c.portrait = scan.portrait
            if not c.legend:
                c.legend = legends.match(scan.portrait)[0]
