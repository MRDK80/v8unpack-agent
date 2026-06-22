"""forms_index — реестр распакованных форм с контролем рассинхрона.

JSON-файл рядом с выгрузкой, с записью по каждой форме. Индекс — **не**
источник истины (источник — ``Form.bin`` в выгрузке), а **карта актуальности**:
по ``bin_mtime`` vs ``unpacked_mtime`` мгновенно видно, для каких форм
распаковка устарела.

В индекс кладётся только то, что нужно для маршрутизации к текстам и проверки
свежести. **Никакого** содержимого ``Form.bin``, никаких строк подключения,
имён баз/хостов — индекс должен оставаться обезличенным, чтобы его можно было
коммитить в репозиторий вместе с выгрузкой.

Структура записи::

    {
      "<ИмяФормы>": {
        "bin_path": "Forms/<ИмяФормы>/Ext/Form.bin",
        "unpacked_root": "unpacked/Form/<ИмяФормы>/",
        "bin_mtime": 1718450000.0,
        "unpacked_mtime": 1718450042.0,
        "extraction_ok": true,
        "warnings": []
      }
    }
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass
class FormsIndexEntry:
    """Одна запись реестра форм."""

    bin_path: str
    unpacked_root: str
    bin_mtime: float
    unpacked_mtime: float
    extraction_ok: bool = True
    warnings: list[str] = field(default_factory=list)


def is_form_stale(idx_entry: FormsIndexEntry | dict) -> bool:
    """Форма устарела, если исходный ``Form.bin`` новее его распаковки.

    Принимает как :class:`FormsIndexEntry`, так и словарь (запись из JSON).
    """
    if isinstance(idx_entry, FormsIndexEntry):
        return idx_entry.bin_mtime > idx_entry.unpacked_mtime
    return float(idx_entry["bin_mtime"]) > float(idx_entry["unpacked_mtime"])


class FormsIndex:
    """In-memory реестр форм с сохранением/загрузкой в JSON."""

    def __init__(self, entries: dict[str, FormsIndexEntry] | None = None) -> None:
        self._entries: dict[str, FormsIndexEntry] = dict(entries or {})

    def upsert(self, form_name: str, entry: FormsIndexEntry) -> None:
        """Добавить или обновить запись по имени формы."""
        self._entries[form_name] = entry

    def get(self, form_name: str) -> FormsIndexEntry | None:
        return self._entries.get(form_name)

    def entries(self) -> dict[str, FormsIndexEntry]:
        return dict(self._entries)

    def stale_forms(self) -> tuple[str, ...]:
        """Имена форм, у которых распаковка устарела (``bin_mtime`` новее)."""
        return tuple(
            sorted(name for name, e in self._entries.items() if is_form_stale(e))
        )

    def to_dict(self) -> dict:
        return {
            name: asdict(entry)
            for name, entry in sorted(self._entries.items())
        }

    def save(self, index_path: Path) -> Path:
        """Записать индекс как UTF-8 JSON (отсортированный, читаемый в diff)."""
        index_path.parent.mkdir(parents=True, exist_ok=True)
        index_path.write_text(
            json.dumps(self.to_dict(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        return index_path

    @classmethod
    def load(cls, index_path: Path) -> "FormsIndex":
        """Загрузить индекс; отсутствующий файл — пустой индекс."""
        if not index_path.exists():
            return cls()
        raw = json.loads(index_path.read_text(encoding="utf-8"))
        entries = {
            name: FormsIndexEntry(
                bin_path=row["bin_path"],
                unpacked_root=row["unpacked_root"],
                bin_mtime=float(row["bin_mtime"]),
                unpacked_mtime=float(row["unpacked_mtime"]),
                extraction_ok=bool(row.get("extraction_ok", True)),
                warnings=list(row.get("warnings", [])),
            )
            for name, row in raw.items()
        }
        return cls(entries)
