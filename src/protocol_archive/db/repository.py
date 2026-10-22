"""Репозиторий: сохранение и поиск документов и протоколов.

Оригиналы только читаются (для хэша и метаданных), никогда не изменяются и не копируются.
"""
from __future__ import annotations

import hashlib
import re
import sqlite3
from dataclasses import dataclass, field, fields
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Sequence, Union

from .connection import has_fts
from .schema import DOCUMENT_STATUSES, OFFENDER_TYPES, PROTOCOL_STATUSES

_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha256_of_file(path: Path, chunk: int = 1024 * 1024) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:  # только чтение
        for block in iter(lambda: f.read(chunk), b""):
            h.update(block)
    return h.hexdigest()


def _check_date(value: Optional[str], name: str) -> Optional[str]:
    if value in (None, ""):
        return None
    if not _ISO_DATE.match(value):
        raise ValueError(f"{name}: ожидается формат ГГГГ-ММ-ДД, получено {value!r}")
    try:
        datetime.strptime(value, "%Y-%m-%d")
    except ValueError as exc:
        raise ValueError(f"{name}: несуществующая дата {value!r}") from exc
    return value


@dataclass
class Document:
    id: int
    path: str
    file_name: str
    extension: str
    size_bytes: int
    mtime_ns: int
    sha256: str
    status: str
    error_message: Optional[str]
    added_at: str
    updated_at: str


@dataclass
class Protocol:
    document_id: int
    id: Optional[int] = None
    protocol_number: Optional[str] = None
    protocol_date: Optional[str] = None
    violation_date: Optional[str] = None
    article: Optional[str] = None
    offender_type: Optional[str] = None   # 'official' | 'legal_entity' | None (не указан)
    offender_name: Optional[str] = None
    location: Optional[str] = None
    gost_norms: Optional[str] = None      # «п. 5.2.4 ГОСТ Р 50597-2017; п. 8.8 …»
    authority: Optional[str] = None
    notes: Optional[str] = None
    status: str = "manual"
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


_PROTOCOL_EDITABLE = [
    "protocol_number", "protocol_date", "violation_date", "article",
    "offender_type", "offender_name", "location", "gost_norms", "authority", "notes", "status",
]


@dataclass
class ProtocolFilter:
    """Условия поиска. Пустые поля игнорируются; все заданные условия объединяются по И."""
    text: str = ""                    # полнотекстовый запрос по извлечённому тексту документа
    protocol_number: str = ""         # подстрока
    article: str = ""                 # подстрока
    offender_name: str = ""           # подстрока
    offender_type: str = ""           # 'official' | 'legal_entity' | '' (любой)
    location: str = ""                # подстрока
    gost: str = ""                    # подстрока в нарушенных пунктах ГОСТ
    date_from: Optional[str] = None   # protocol_date >= (ГГГГ-ММ-ДД)
    date_to: Optional[str] = None     # protocol_date <= (ГГГГ-ММ-ДД)
    status: str = ""


@dataclass
class ProtocolRow:
    """Протокол вместе со сведениями об оригинале (для таблиц интерфейса)."""
    protocol: Protocol
    document_path: str
    document_name: str


def _escape_like(s: str) -> str:
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _fts_query(text: str) -> str:
    """Превращает ввод пользователя в безопасный FTS5-запрос (все слова, префиксный поиск)."""
    words = re.findall(r"\w+", text, flags=re.UNICODE)
    return " ".join('"' + w.replace('"', '""') + '"*' for w in words)


class Repository:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def _text_condition(self, text: str, id_column: str):
        """SQL-условие «документ содержит все слова запроса» или None для пустого запроса.

        Возвращает (sql, args); при запросе без слов — ("0", []) (ничего не найдено).
        """
        words = re.findall(r"\w+", text.lower(), flags=re.UNICODE)
        if not text.strip():
            return None
        if not words:
            return "0", []
        if has_fts(self.conn):
            return (f"{id_column} IN (SELECT rowid FROM document_texts_fts"
                    " WHERE document_texts_fts MATCH ?)", [_fts_query(text)])
        # запасной вариант без FTS5: все слова как подстроки
        sql = " AND ".join(
            f"{id_column} IN (SELECT document_id FROM document_texts"
            " WHERE ulower(text) LIKE ? ESCAPE '\\')" for _ in words)
        return sql, ["%" + _escape_like(w) + "%" for w in words]

    # ----- документы -----------------------------------------------------

    @staticmethod
    def _doc(row: sqlite3.Row) -> Document:
        return Document(**{k: row[k] for k in row.keys()})

    def add_document(self, path: Union[str, Path]) -> Document:
        """Регистрирует оригинал по пути. Если путь уже есть — обновляет метаданные.

        Файл только читается. Если содержимое изменилось (другой хэш), статус
        сбрасывается в 'new', потому что ранее извлечённые сведения могли устареть.
        """
        p = Path(path).expanduser().resolve()
        if not p.is_file():
            raise FileNotFoundError(f"Файл не найден: {p}")
        st = p.stat()
        digest = sha256_of_file(p)
        now = _now()
        ext = p.suffix.lower().lstrip(".")
        existing = self.get_document_by_path(p)
        with self.conn:
            if existing is None:
                cur = self.conn.execute(
                    "INSERT INTO documents(path,file_name,extension,size_bytes,mtime_ns,"
                    "sha256,status,added_at,updated_at) VALUES(?,?,?,?,?,?,'new',?,?)",
                    (str(p), p.name, ext, st.st_size, st.st_mtime_ns, digest, now, now),
                )
                doc_id = cur.lastrowid
            else:
                doc_id = existing.id
                status = existing.status
                # Содержимое изменилось или файл снова появился: прежние сведения
                # могли устареть, поэтому документ нужно обработать заново.
                if existing.sha256 != digest or status == "missing":
                    status = "new"
                self.conn.execute(
                    "UPDATE documents SET file_name=?, extension=?, size_bytes=?, mtime_ns=?,"
                    " sha256=?, status=?, error_message=NULL, updated_at=? WHERE id=?",
                    (p.name, ext, st.st_size, st.st_mtime_ns, digest, status, now, doc_id),
                )
        return self.get_document(doc_id)  # type: ignore[return-value]

    def get_document(self, doc_id: int) -> Optional[Document]:
        row = self.conn.execute("SELECT * FROM documents WHERE id=?", (doc_id,)).fetchone()
        return self._doc(row) if row else None

    def get_document_by_path(self, path: Union[str, Path]) -> Optional[Document]:
        p = str(Path(path).expanduser().resolve())
        row = self.conn.execute("SELECT * FROM documents WHERE path=?", (p,)).fetchone()
        return self._doc(row) if row else None

    def find_documents_by_hash(self, sha256: str) -> List[Document]:
        rows = self.conn.execute(
            "SELECT * FROM documents WHERE sha256=? ORDER BY id", (sha256,)
        ).fetchall()
        return [self._doc(r) for r in rows]

    def list_documents(self, status: str = "", name_contains: str = "", text: str = "",
                       limit: int = 1000, offset: int = 0) -> List[Document]:
        sql = "SELECT * FROM documents WHERE 1=1"
        args: list = []
        cond = self._text_condition(text, "id")
        if cond:
            sql += " AND " + cond[0]
            args += cond[1]
        if status:
            sql += " AND status=?"
            args.append(status)
        if name_contains:
            sql += " AND ulower(file_name) LIKE ? ESCAPE '\\'"
            args.append("%" + _escape_like(name_contains.lower()) + "%")
        sql += " ORDER BY id DESC LIMIT ? OFFSET ?"
        args += [limit, offset]
        return [self._doc(r) for r in self.conn.execute(sql, args).fetchall()]

    def count_documents(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]

    def set_document_status(self, doc_id: int, status: str,
                            error_message: Optional[str] = None) -> None:
        if status not in DOCUMENT_STATUSES:
            raise ValueError(f"Недопустимый статус документа: {status!r}")
        with self.conn:
            cur = self.conn.execute(
                "UPDATE documents SET status=?, error_message=?, updated_at=? WHERE id=?",
                (status, error_message, _now(), doc_id),
            )
        if cur.rowcount == 0:
            raise KeyError(f"Документ {doc_id} не найден")

    def delete_document(self, doc_id: int) -> None:
        """Удаляет запись из базы (вместе с протоколами и текстом). Файл не затрагивается."""
        with self.conn:
            self.conn.execute("DELETE FROM documents WHERE id=?", (doc_id,))

    def check_missing_documents(self) -> int:
        """Помечает статусом 'missing' документы, чьи файлы больше не существуют."""
        count = 0
        for row in self.conn.execute("SELECT id, path, status FROM documents").fetchall():
            if not Path(row["path"]).exists() and row["status"] != "missing":
                self.set_document_status(row["id"], "missing")
                count += 1
        return count

    # ----- текст документов ----------------------------------------------

    def set_document_text(self, doc_id: int, text: str, source: str) -> None:
        if self.get_document(doc_id) is None:
            raise KeyError(f"Документ {doc_id} не найден")
        with self.conn:
            self.conn.execute(
                "INSERT INTO document_texts(document_id,text,source,extracted_at)"
                " VALUES(?,?,?,?) ON CONFLICT(document_id) DO UPDATE SET"
                " text=excluded.text, source=excluded.source,"
                " extracted_at=excluded.extracted_at",
                (doc_id, text, source, _now()),
            )

    def delete_document_text(self, doc_id: int) -> None:
        with self.conn:
            self.conn.execute("DELETE FROM document_texts WHERE document_id=?", (doc_id,))

    def get_document_text_info(self, doc_id: int):
        """(текст, источник, время извлечения) или None."""
        row = self.conn.execute(
            "SELECT text, source, extracted_at FROM document_texts WHERE document_id=?",
            (doc_id,)).fetchone()
        return (row["text"], row["source"], row["extracted_at"]) if row else None

    def list_document_ids(self, statuses: Sequence[str]) -> List[int]:
        marks = ",".join("?" * len(statuses))
        rows = self.conn.execute(
            f"SELECT id FROM documents WHERE status IN ({marks}) ORDER BY id", list(statuses))
        return [r["id"] for r in rows.fetchall()]

    def get_document_text(self, doc_id: int) -> Optional[str]:
        row = self.conn.execute(
            "SELECT text FROM document_texts WHERE document_id=?", (doc_id,)
        ).fetchone()
        return row["text"] if row else None

    # ----- протоколы -------------------------------------------------------

    @staticmethod
    def _protocol(row: sqlite3.Row) -> Protocol:
        names = {f.name for f in fields(Protocol)}
        return Protocol(**{k: row[k] for k in row.keys() if k in names})

    def _validated(self, p: Protocol) -> dict:
        if p.status not in PROTOCOL_STATUSES:
            raise ValueError(f"Недопустимый статус протокола: {p.status!r}")
        data = {name: getattr(p, name) for name in _PROTOCOL_EDITABLE}
        data["protocol_date"] = _check_date(data["protocol_date"], "Дата протокола")
        data["violation_date"] = _check_date(data["violation_date"], "Дата нарушения")
        for key, val in data.items():
            if isinstance(val, str):
                val = val.strip()
                data[key] = val or None
        data["status"] = p.status
        if data["offender_type"] is not None and data["offender_type"] not in OFFENDER_TYPES:
            raise ValueError(
                "Тип лица: допустимы только должностное лицо или юридическое лицо, "
                f"получено {data['offender_type']!r}")
        return data

    def add_protocol(self, protocol: Protocol) -> Protocol:
        if self.get_document(protocol.document_id) is None:
            raise KeyError(f"Документ {protocol.document_id} не найден")
        data = self._validated(protocol)
        now = _now()
        cols = ["document_id"] + _PROTOCOL_EDITABLE + ["created_at", "updated_at"]
        vals = [protocol.document_id] + [data[c] for c in _PROTOCOL_EDITABLE] + [now, now]
        with self.conn:
            cur = self.conn.execute(
                f"INSERT INTO protocols({','.join(cols)}) VALUES({','.join('?' * len(cols))})",
                vals,
            )
        return self.get_protocol(cur.lastrowid)  # type: ignore[arg-type,return-value]

    def update_protocol(self, protocol: Protocol) -> Protocol:
        if protocol.id is None:
            raise ValueError("У протокола не задан id")
        data = self._validated(protocol)
        sets = ",".join(f"{c}=?" for c in _PROTOCOL_EDITABLE)
        with self.conn:
            cur = self.conn.execute(
                f"UPDATE protocols SET {sets}, updated_at=? WHERE id=?",
                [data[c] for c in _PROTOCOL_EDITABLE] + [_now(), protocol.id],
            )
        if cur.rowcount == 0:
            raise KeyError(f"Протокол {protocol.id} не найден")
        return self.get_protocol(protocol.id)  # type: ignore[return-value]

    def get_protocol(self, protocol_id: int) -> Optional[Protocol]:
        row = self.conn.execute("SELECT * FROM protocols WHERE id=?", (protocol_id,)).fetchone()
        return self._protocol(row) if row else None

    def list_protocols_for_document(self, doc_id: int) -> List[Protocol]:
        rows = self.conn.execute(
            "SELECT * FROM protocols WHERE document_id=? ORDER BY id", (doc_id,)).fetchall()
        return [self._protocol(r) for r in rows]

    def set_evidence(self, protocol_id: int, items: Sequence[tuple]) -> None:
        """Заменяет записи о происхождении значений. items: (поле, значение, фрагмент, правило)."""
        with self.conn:
            self.conn.execute("DELETE FROM protocol_evidence WHERE protocol_id=?", (protocol_id,))
            self.conn.executemany(
                "INSERT INTO protocol_evidence(protocol_id,field,value,evidence,rule)"
                " VALUES(?,?,?,?,?)", [(protocol_id, *it) for it in items])

    def get_evidence(self, protocol_id: int) -> List[tuple]:
        rows = self.conn.execute(
            "SELECT field,value,evidence,rule FROM protocol_evidence WHERE protocol_id=? ORDER BY id",
            (protocol_id,)).fetchall()
        return [tuple(r) for r in rows]

    def list_document_ids_without_protocol(self, status: str = "processed") -> List[int]:
        rows = self.conn.execute(
            "SELECT d.id FROM documents d WHERE d.status=? AND NOT EXISTS"
            " (SELECT 1 FROM protocols p WHERE p.document_id=d.id) ORDER BY d.id", (status,))
        return [r["id"] for r in rows.fetchall()]

    def delete_protocol(self, protocol_id: int) -> None:
        with self.conn:
            self.conn.execute("DELETE FROM protocols WHERE id=?", (protocol_id,))

    def count_protocols(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM protocols").fetchone()[0]

    def search_protocols(self, flt: Optional[ProtocolFilter] = None,
                         limit: int = 1000, offset: int = 0) -> List[ProtocolRow]:
        flt = flt or ProtocolFilter()
        where: List[str] = []
        args: list = []

        for column, value in (
            ("protocol_number", flt.protocol_number),
            ("article", flt.article),
            ("offender_name", flt.offender_name),
            ("location", flt.location),
            ("gost_norms", flt.gost),
        ):
            if value.strip():
                where.append(f"ulower(p.{column}) LIKE ? ESCAPE '\\'")
                args.append("%" + _escape_like(value.strip().lower()) + "%")

        d_from = _check_date(flt.date_from, "Дата с")
        d_to = _check_date(flt.date_to, "Дата по")
        if d_from:
            where.append("p.protocol_date >= ?")
            args.append(d_from)
        if d_to:
            where.append("p.protocol_date <= ?")
            args.append(d_to)
        if flt.offender_type:
            if flt.offender_type not in OFFENDER_TYPES:
                raise ValueError(f"Недопустимый тип лица: {flt.offender_type!r}")
            where.append("p.offender_type = ?")
            args.append(flt.offender_type)
        if flt.status:
            where.append("p.status = ?")
            args.append(flt.status)

        cond = self._text_condition(flt.text, "p.document_id")
        if cond:
            where.append(cond[0])
            args += cond[1]

        sql = (
            "SELECT p.*, d.path AS _doc_path, d.file_name AS _doc_name "
            "FROM protocols p JOIN documents d ON d.id = p.document_id"
        )
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY p.protocol_date DESC, p.id DESC LIMIT ? OFFSET ?"
        args += [limit, offset]

        result = []
        for row in self.conn.execute(sql, args).fetchall():
            result.append(ProtocolRow(self._protocol(row), row["_doc_path"], row["_doc_name"]))
        return result
