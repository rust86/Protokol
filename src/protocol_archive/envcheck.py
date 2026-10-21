"""Проверка доступности зависимостей. Ничего не устанавливает и не изменяет."""
from __future__ import annotations

import importlib
import shutil
import sqlite3
import subprocess
import sys
from dataclasses import dataclass
from typing import List

MIN_PYTHON = (3, 9)


@dataclass
class CheckResult:
    name: str
    ok: bool
    detail: str
    required: bool = True   # обязательна ли на этапе 1
    hint: str = ""          # что сделать, если не найдено


def _run(cmd: List[str], timeout: int = 30) -> str:
    out = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    return (out.stdout + out.stderr).strip()


def _dist_version(dist: str) -> str:
    from importlib import metadata
    try:
        return metadata.version(dist)
    except metadata.PackageNotFoundError:
        return ""


def _check_module(label: str, modules: List[str], pip_name: str, required: bool = True) -> CheckResult:
    """Пробует импортировать модули по очереди (у PyMuPDF старое имя — fitz)."""
    last_exc: Exception = ImportError(modules[0])
    for module in modules:
        try:
            importlib.import_module(module)
        except Exception as exc:  # ImportError и ошибки загрузки бинарных библиотек
            last_exc = exc
            continue
        version = _dist_version(pip_name)
        return CheckResult(label, True, f"найден {version}".strip(), required)
    return CheckResult(label, False, f"не импортируется: {last_exc}", required,
                       f"pip install {pip_name} (внутри виртуального окружения)")


def check_python() -> CheckResult:
    v = sys.version_info
    ok = (v.major, v.minor) >= MIN_PYTHON
    return CheckResult("Python", ok, f"{v.major}.{v.minor}.{v.micro} ({sys.executable})",
                       True, f"нужен Python не ниже {MIN_PYTHON[0]}.{MIN_PYTHON[1]}")


def check_sqlite() -> List[CheckResult]:
    res = [CheckResult("SQLite", sqlite3.sqlite_version_info >= (3, 24),
                       sqlite3.sqlite_version, True, "нужна версия 3.24 или новее (UPSERT)")]
    conn = sqlite3.connect(":memory:")
    try:
        conn.execute("CREATE VIRTUAL TABLE t USING fts5(x)")
        res.append(CheckResult("SQLite FTS5", True, "доступен", False))
    except sqlite3.OperationalError:
        res.append(CheckResult(
            "SQLite FTS5", False, "недоступен: полнотекстовый поиск будет работать медленнее "
            "(по подстрокам)", False, "используйте Python со сборкой SQLite с FTS5"))
    finally:
        conn.close()
    return res


def check_tesseract() -> List[CheckResult]:
    exe = shutil.which("tesseract")
    if not exe:
        return [CheckResult("Tesseract OCR", False, "не найден в PATH", False,
                            "Ubuntu: sudo apt install tesseract-ocr tesseract-ocr-rus")]
    try:
        version = _run([exe, "--version"]).splitlines()[0]
        langs = [ln.strip() for ln in _run([exe, "--list-langs"]).splitlines()
                 if ln.strip() and not ln.startswith("List of available")]
    except Exception as exc:
        return [CheckResult("Tesseract OCR", False, f"ошибка запуска: {exc}", False)]
    res = [CheckResult("Tesseract OCR", True, f"{version} ({exe})", False)]
    has_rus = "rus" in langs
    res.append(CheckResult(
        "Tesseract: язык rus", has_rus,
        "установлен" if has_rus else "русский языковой пакет не найден (есть: " + ", ".join(langs) + ")", False,
        "Ubuntu: sudo apt install tesseract-ocr-rus; ALT: найдите пакет командой "
        "apt-cache search tesseract | grep -i rus"))
    return res


def check_libreoffice() -> CheckResult:
    exe = shutil.which("soffice") or shutil.which("libreoffice")
    if not exe:
        return CheckResult("LibreOffice", False, "soffice не найден в PATH", False,
                           "Ubuntu: sudo apt install libreoffice-writer; "
                           "ALT: apt-get install LibreOffice-still (или LibreOffice)")
    try:
        return CheckResult("LibreOffice", True, f"{_run([exe, '--version'], 60)} ({exe})", False)
    except Exception as exc:
        return CheckResult("LibreOffice", False, f"ошибка запуска: {exc}", False)


def check_all() -> List[CheckResult]:
    results: List[CheckResult] = [check_python()]
    results += check_sqlite()
    results.append(_check_module("PySide6 (Qt)", ["PySide6"], "PySide6"))
    # Библиотеки обработки файлов нужны с этапа 2; на этапе 1 не обязательны.
    results.append(_check_module("PyMuPDF", ["pymupdf", "fitz"], "PyMuPDF", required=False))
    results.append(_check_module("python-docx", ["docx"], "python-docx", required=False))
    results.append(_check_module("odfpy", ["odf"], "odfpy", required=False))
    results += check_tesseract()
    results.append(check_libreoffice())
    return results


def format_report(results: List[CheckResult]) -> str:
    lines = []
    for r in results:
        mark = "OK  " if r.ok else ("FAIL" if r.required else "WARN")
        tail = "" if r.ok or not r.hint else f"\n        → {r.hint}"
        lines.append(f"[{mark}] {r.name}: {r.detail}{tail}")
    return "\n".join(lines)


def main() -> int:
    results = check_all()
    print(format_report(results))
    failed = [r for r in results if r.required and not r.ok]
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
