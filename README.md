# Архив протоколов (MVP, этап 1)

Локальный архив протоколов об административных правонарушениях. Работает без облачных
сервисов и без ИИ-моделей. **Оригиналы документов не копируются и не изменяются** — они
только читаются (для вычисления хэша и размера). В базе SQLite хранятся извлечённые
сведения и пути к оригиналам.

## Что реализовано на этапе 1

| Возможность | Состояние |
|---|---|
| Схема SQLite (документы, протоколы, текст документов, полнотекстовый индекс FTS5) | готово |
| Подключение, создание таблиц, контроль версии схемы | готово |
| Репозиторий: добавление/обновление/удаление, поиск по реквизитам, датам и тексту | готово |
| Резервное копирование базы с проверкой целостности и ротацией | готово |
| Главное окно на русском языке, ручной ввод протоколов, поиск | готово |
| Проверка зависимостей (`--check` и меню «Справка») | готово |
| Извлечение текста из PDF (PyMuPDF), OCR (Tesseract) | **не реализовано (этап 2)** |
| Чтение DOCX / ODT / DOC (python-docx, odfpy, LibreOffice) | **не реализовано (этап 2)** |
| Автоматическое извлечение реквизитов правилами и регулярными выражениями | **не реализовано (этап 3)** |
| Восстановление базы из копии | **не реализовано** (копию можно подставить вручную, см. ниже) |

Пока автоматического извлечения нет, документы только регистрируются по пути
(статус «Добавлен, не обработан»), а реквизиты протоколов вносятся вручную. Программа не
подставляет никаких значений сама: незаполненные поля остаются пустыми.

Библиотеки PyMuPDF, python-docx, odfpy, а также Tesseract и LibreOffice на этапе 1 не
используются в работе; они проверяются и ставятся заранее, чтобы этап 2 не требовал
повторной настройки окружения.

## Требования

* Python 3.9 или новее (с модулем `venv`);
* SQLite 3.24+ (для полнотекстового поиска нужна сборка с FTS5 — в Python из Ubuntu она есть;
  без FTS5 поиск по тексту работает медленнее, по подстрокам);
* pip-пакеты (ставятся в виртуальное окружение): PySide6, PyMuPDF, python-docx, odfpy, pytest;
* системные программы (нужны с этапа 2): Tesseract с языком `rus`, LibreOffice (`soffice`).

## Установка без изменения системного Python

Все pip-пакеты ставятся только в папку проекта `.venv`. Системные пакеты (`apt`) нужны
только для Tesseract, LibreOffice и библиотек Qt, а системный Python не меняется.

### Ubuntu (22.04 / 24.04)

```bash
# 1. Системные пакеты (нужны права администратора)
sudo apt update
sudo apt install -y python3 python3-venv python3-pip \
    libegl1 libgl1 libxkbcommon-x11-0 libxcb-cursor0 libdbus-1-3 \
    tesseract-ocr tesseract-ocr-rus libreoffice-writer

# 2. Виртуальное окружение и зависимости (без sudo)
cd protocol-archive
scripts/install.sh
```

### ALT Linux

Точные имена пакетов в ALT зависят от ветки (p10, p11, Sisyphus), в этой работе они на ALT
**не проверялись**. Сначала найдите их:

```bash
apt-cache search tesseract | grep -i -E 'tesseract|rus'
apt-cache search libreoffice | head
python3 --version
python3 -c "import venv, ensurepip" && echo "venv доступен"
```

Затем установите найденные пакеты (пример; подставьте реальные имена из вывода выше):

```bash
su -
apt-get update
apt-get install tesseract tesseract-langpack-rus LibreOffice   # имена проверьте через apt-cache
exit
```

Установка проекта такая же, как в Ubuntu:

```bash
cd protocol-archive
scripts/install.sh
```

Если pip не может установить PySide6 (нет колёс под вашу версию Python или glibc), поставьте
`PySide6` из репозитория ALT (поищите `apt-cache search pyside6`) и запустите установку так,
чтобы окружение видело системные пакеты — системный Python при этом не меняется:

```bash
scripts/install.sh --system-site-packages
```

### Старые процессоры (без SSE4.2 / POPCNT, например Core 2 Duo)

Колёса PySide6 с PyPI на таких процессорах аварийно завершаются при запуске
(`Incompatible processor. This Qt build requires the following features: sse4.2 popcnt`).
Используйте Qt из репозитория дистрибутива:

```bash
apt-cache search -i pyside6                      # найдите пакет PySide6 для Python 3
su -
apt-get install <найденный пакет PySide6>
exit
rm -rf .venv
python3 -m venv --system-site-packages .venv
.venv/bin/pip install -e . --no-deps
.venv/bin/pip install "PyMuPDF>=1.23,<2" python-docx odfpy pytest
.venv/bin/python -c "from PySide6.QtWidgets import QApplication; print('Qt работает')"
```

PyMuPDF закреплён на версии 1.x: версия 2.0 тянет `onnxruntime` и `numpy` (модули машинного
обучения), которые проекту не нужны.

### Установка вручную (то же, что делает `scripts/install.sh`)

```bash
cd protocol-archive
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -e ".[dev]"
```

## Проверка зависимостей

```bash
.venv/bin/python -m protocol_archive --check
```

Выводит `OK` / `WARN` / `FAIL` для каждой зависимости и подсказку, что установить. Код
возврата 1 только при отсутствии обязательного для этапа 1 (Python, SQLite, PySide6).
Нехватка Tesseract / `rus` / LibreOffice на этапе 1 даёт `WARN`. Ту же проверку можно
открыть в программе: «Справка → Проверка зависимостей…».

## Запуск

```bash
.venv/bin/protocol-archive
# или
.venv/bin/python -m protocol_archive
# с указанием файла базы
.venv/bin/protocol-archive --db ~/archive/protocols.sqlite3
```

База по умолчанию: `~/.local/share/protocol-archive/archive.sqlite3`
(учитывается `XDG_DATA_HOME`). Переменные окружения: `PROTOCOL_ARCHIVE_DB` — путь к базе,
`PROTOCOL_ARCHIVE_BACKUPS` — папка резервных копий.

Запуск без графической сессии (например, по SSH) для проверки:
`QT_QPA_PLATFORM=offscreen .venv/bin/protocol-archive`.

## Тесты

```bash
.venv/bin/python -m pytest
```

Тесты окна выполняются в режиме `offscreen` и не требуют экрана. Если PySide6 не
установлен, тесты окна пропускаются (это видно в итоговой строке pytest).

## Резервные копии

«Файл → Создать резервную копию базы» (Ctrl+B). Копия создаётся средствами SQLite
(безопасно при открытой базе), проверяется `PRAGMA integrity_check` и сохраняется как
`archive_ГГГГММДД_ЧЧММСС.sqlite3` в папке `backups` рядом с базой; хранятся 10 последних.
Копируется только база — не оригиналы документов.

Восстановление вручную: закройте программу и замените файл базы нужной копией
(удалите также файлы `*.sqlite3-wal` и `*.sqlite3-shm` рядом с базой).

## Структура проекта

```
protocol-archive/
├── pyproject.toml            зависимости, точка входа protocol-archive
├── scripts/install.sh        установка в .venv
├── src/protocol_archive/
│   ├── __main__.py           командная строка (--db, --check, --version)
│   ├── config.py             пути по умолчанию
│   ├── dates.py              ДД.ММ.ГГГГ ⇄ ГГГГ-ММ-ДД
│   ├── envcheck.py           проверка зависимостей
│   ├── db/
│   │   ├── schema.py         DDL, версия схемы
│   │   ├── connection.py     подключение, инициализация
│   │   ├── repository.py     сохранение и поиск
│   │   └── backup.py         резервное копирование
│   └── ui/
│       ├── app.py            запуск QApplication
│       ├── main_window.py    главное окно
│       └── dialogs.py        ввод/правка протокола
└── tests/                    pytest
```

## Модель данных

Версия схемы — 2. База версии 1 (с полем госномера) при открытии автоматически
мигрируется: поле `vehicle_plate` удаляется вместе со значениями, тип лица остаётся пустым
(программа не угадывает его). Перед обновлением рекомендуется сделать резервную копию (Ctrl+B).

* `documents` — путь к оригиналу (уникален), имя, тип, размер, время изменения, SHA-256,
  статус (`new`, `processed`, `error`, `missing`).
* `protocols` — реквизиты протокола, связь с документом; даты хранятся как `ГГГГ-ММ-ДД`,
  в интерфейсе показываются как `ДД.ММ.ГГГГ`. Протоколы ведутся только в отношении
  должностных и юридических лиц: поле `offender_type` принимает `official` (должностное
  лицо), `legal_entity` (юридическое лицо) или пусто (не указан), а `offender_name` — это
  ФИО должностного лица или наименование юрлица. Статус записи: `manual`, `extracted`,
  `verified`.
* `document_texts` + `document_texts_fts` — извлечённый текст и полнотекстовый индекс
  (заполняются начиная с этапа 2).

Если файл-оригинал изменился (другой хэш), при повторном добавлении документ получает
статус `new`, так как ранее извлечённые сведения могли устареть.
