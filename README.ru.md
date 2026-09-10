# VMware Guest Bridge

Мост для управления виртуалками VMware из терминала на Windows, Linux и macOS. Запускает PowerShell в Windows и shell-скрипты в Linux и других POSIX-гостях. Переносит файлы, снимает экран виртуалки, отправляет клавиши и клики. Мышь и клавиатура хоста остаются свободными.

[English](README.md) · [Системы и настройка Linux](docs/platforms.md) · [Команды](docs/cli.md) · [JSON-протокол](docs/protocol.md)

Для команд и файлов нужны VMware Tools (`open-vm-tools` в Linux) и учётная запись гостя. Для экрана и ввода используется отдельное подключение к локальной RFB-консоли VMware. IP-адрес гостя, SSH и общие папки не требуются.

## Установка

Нужны Python 3.11+, уже запущенная виртуалка и VMware с `vmrun`: Workstation на Windows/Linux либо Fusion на macOS. Python устанавливается только на хосте. В Windows-госте нужен PowerShell 5.1, в POSIX-госте — `/bin/sh` и `mv`.

На хосте Windows, в PowerShell:

```powershell
git clone https://github.com/ShiroKSH/vmware-guest-bridge.git
cd vmware-guest-bridge
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install .
.\.venv\Scripts\vmb.exe init
```

Без Git: скачай **Code → Download ZIP**, распакуй архив и выполни последние три команды из его папки.

На хосте Linux или macOS, для Linux-гостя:

```sh
git clone https://github.com/ShiroKSH/vmware-guest-bridge.git
cd vmware-guest-bridge
python3 -m venv .venv
.venv/bin/python -m pip install .
.venv/bin/vmb --guest-os linux init
```

Система хоста и система гостя независимы: с Windows можно управлять Linux, с Linux — Windows. `init` по умолчанию создаёт конфиг Windows-гостя; для другой системы добавь перед ним `--guest-os linux`, `macos`, `freebsd` или `posix`. Профили POSIX требуют совместимости самой гостевой системы с VMware и Tools; список профилей не отменяет ограничений гипервизора.

В `bridge.toml` укажи путь `vmx`, логин `username` и папку `guest_root` внутри виртуалки. Для POSIX нужен абсолютный путь к личной папке, например `/home/guest-user/.guest-bridge`; создай её внутри гостя с правами `700`. Пароль в конфиг не записывается: команды запрашивают его скрытым вводом.

Запуск PowerShell в Windows-госте:

```powershell
.\.venv\Scripts\vmb.exe --config bridge.toml doctor
.\.venv\Scripts\vmb.exe --config bridge.toml status
New-Item -ItemType Directory -Path .guest-bridge -Force
Copy-Item examples\hello.ps1 .guest-bridge\hello.ps1
.\.venv\Scripts\vmb.exe --config bridge.toml ps --file hello.ps1
```

Запуск shell в Linux/POSIX-госте с Linux/macOS-хоста:

```sh
.venv/bin/vmb --config bridge.toml status
mkdir -p .guest-bridge
cp examples/hello.sh .guest-bridge/hello.sh
.venv/bin/vmb --config bridge.toml sh --file hello.sh
```

С Windows-хоста команда та же: `sh --file hello.sh`, но путь к программе — `.\.venv\Scripts\vmb.exe`. Shell возвращает `stdout`, `stderr`, `exit_code` и идентификатор запроса. Для Bash задай `guest_shell = '/bin/bash'`. Образцы конфигов, установка Tools и ограничения Apple Silicon: [docs/platforms.md](docs/platforms.md).

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
- После тайм-аута команда может продолжать работать. Для `ps` и `sh` получай результат через `result REQUEST_ID` с тем же конфигом, прежде чем повторять запуск.
- Приложения с окном запускай через `ps --interactive`, когда пользователь уже вошёл в гостевую Windows.
- `vmrun` передаёт гостевой пароль в аргументах своего процесса. Скрытый ввод не защищает его от привилегированных средств на хосте.
- Скрипты и ответы сохраняются в `.vmb` внутри обеих рабочих папок. PowerShell и shell имеют права гостевого пользователя; ограничение путей не является песочницей.

Проверки: [docs/testing.md](docs/testing.md). Ошибки и зависания: [docs/troubleshooting.md](docs/troubleshooting.md). Лицензия: [MIT](LICENSE).
