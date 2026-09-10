# VMware Guest Bridge

Мост для управления Windows в VMware из терминала. Запускает PowerShell, переносит файлы, снимает экран виртуалки, отправляет нажатия клавиш и клики. Мышь и клавиатура основной Windows остаются свободными.

[English](README.md) · [Команды](docs/cli.md) · [JSON-протокол](docs/protocol.md)

Для команд и файлов нужны VMware Tools и учётная запись гостевой Windows. Для экрана и ввода используется отдельное подключение к локальной RFB-консоли VMware. IP-адрес гостя, SSH и общие папки не требуются.

## Установка

Нужны Windows, VMware Workstation с `vmrun.exe`, Python 3.11+ и уже запущенная Windows-виртуалка.

Скачай или клонируй репозиторий. В PowerShell из его папки:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install .
.\.venv\Scripts\vmb.exe init
```

В созданном `bridge.toml` укажи путь `vmx` и логин `username` внутри виртуалки. Пароль в конфиг не записывается: обычные команды запрашивают его скрытым вводом.

```powershell
.\.venv\Scripts\vmb.exe --config bridge.toml doctor
.\.venv\Scripts\vmb.exe --config bridge.toml status
New-Item -ItemType Directory -Path .guest-bridge -Force
Copy-Item examples\hello.ps1 .guest-bridge\hello.ps1
.\.venv\Scripts\vmb.exe --config bridge.toml ps --file hello.ps1
```

`host_root` — папка файлов на хосте, `guest_root` — внутри виртуалки. Имена в командах передачи файлов задаются относительно этих папок. Перезапись требует `--overwrite`.

## Экран и ввод

Сначала выполни [настройку консоли](docs/console.md). В этой версии поддерживается только RFB без пароля на loopback-адресе; доступ включается явно через `allow_unauthenticated`. На компьютере с недоверенными локальными пользователями такой режим не подходит.

```powershell
.\.venv\Scripts\vmb.exe --config bridge.toml console screenshot --target screen.png
.\.venv\Scripts\vmb.exe --config bridge.toml console key Ctrl+Alt+Delete
.\.venv\Scripts\vmb.exe --config bridge.toml console type --text "Hello"
```

Для ввода текста выбери английскую раскладку US внутри гостя. Для чувствительного текста используй `console type --secret`. Для автоматизации есть постоянный JSON-канал `serve`; он не открывает серверный порт и не запрашивает пароли интерактивно.

## Ограничения

- Мост сам не запускает виртуалки и не меняет их настройки.
- После тайм-аута команда может продолжать работать. Получай результат по старому идентификатору через `result REQUEST_ID`, прежде чем повторять запуск.
- Приложения с окном запускай через `ps --interactive`, когда пользователь уже вошёл в гостевую Windows.
- `vmrun` передаёт гостевой пароль в аргументах своего процесса. Скрытый ввод не защищает его от привилегированных средств на хосте.
- Скрипты и ответы сохраняются в `.vmb` внутри обеих рабочих папок. PowerShell имеет права гостевого пользователя; ограничение путей не является песочницей.

Проверки: [docs/testing.md](docs/testing.md). Ошибки и зависания: [docs/troubleshooting.md](docs/troubleshooting.md). Лицензия: [MIT](LICENSE).
