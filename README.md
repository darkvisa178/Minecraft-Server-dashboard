# 🎮 Minecraft Server Dashboard

Веб-интерфейс для управления Minecraft сервером через Docker.  
Web interface to manage a Minecraft server via Docker.

---

## 🇷🇺 Русский

### Возможности

- **Запуск / остановка** сервера одной кнопкой (запуск без пароля на Dashboard, остановка — только из Console)
- **Лог в реальном времени** — SSE поток лога сервера с авто-скроллом
- **Отправка команд** в консоль сервера (через `tmux send-keys` + RCON fallback)
- **Отслеживание игроков** — кто онлайн, когда зашёл/вышел
- **Авто-стоп** — сервер выключается через 15 минут бездействия (с предупреждением в чат)
- **Две вкладки**: Dashboard (публичная) и Console (требует пароль)
- **RCON** — команды дублируются через RCON, если `tmux send-keys` недоступен
- **SSH key auth** — при первом запуске генерируется ED25519 ключ, пароль не хранится в процессах
- **Rate limiting** — защита от brute force на странице логина
- **Security headers** — CSP, X-Frame-Options и другие

### Требования

- Docker + Docker Compose
- SSH доступ к хосту (пароль или ключ)
- `sudo` без пароля для `./start.sh` (или настроенный `NOPASSWD`)

### Установка

```bash
git clone https://github.com/darkvisa178/Minecraft-Server-dashboard.git
cd Minecraft-Server-dashboard

# Создать .env с паролями (см. .env.example)
cp .env.example .env
# Отредактировать .env — указать SSH_PASSWORD, CONSOLE_PASSWORD и т.д.

# Создать папку для SSH ключа
mkdir -p ssh

# Запустить
docker compose up -d --build
```

При первом запуске контейнер сгенерирует ED25519 ключ и автоматически добавит его в `~/.ssh/authorized_keys` на хосте (используя пароль из `.env`).  
После этого `sshpass` больше не используется.

### Переменные окружения (.env)

| Переменная | Описание | По умолчанию |
|---|---|---|
| `SSH_PASSWORD` | Пароль SSH (и sudo) | — |
| `CONSOLE_PASSWORD` | Пароль для входа в веб-консоль | = SSH_PASSWORD |
| `RCON_PASSWORD` | Пароль для RCON | = SSH_PASSWORD |
| `SECRET_KEY` | Ключ сессий Flask | случайный |
| `HOST_IP` | IP хоста из контейнера | `172.17.0.1` |
| `SSH_USER` | Пользователь SSH | `user` |
| `MINECRAFT_DIR` | Папка с сервером | `/home/user/minecraft` |

### Структура

```
├── app.py                 # Flask сервер
├── Dockerfile             # Образ контейнера
├── docker-compose.yml     # Docker Compose
├── templates/index.html   # Веб-интерфейс (SPA)
├── ssh/                   # SSH ключи (gitignored)
├── .env                   # Секреты (gitignored)
└── .env.example           # Пример конфига
```

---

## 🇬🇧 English

### Features

- **Start / Stop** the server with one click (start without password on Dashboard, stop requires Console auth)
- **Live log** — SSE stream of server log with auto-scroll
- **Send commands** to server console (via `tmux send-keys` + RCON fallback)
- **Player tracking** — who's online, join/leave events
- **Auto-shutdown** — server stops after 15 minutes of inactivity (with chat warning)
- **Two tabs**: Dashboard (public) and Console (password required)
- **RCON** — commands fall back to RCON when `tmux send-keys` is unavailable
- **SSH key auth** — ED25519 key generated on first run, password never appears in process lists
- **Rate limiting** — brute force protection on login
- **Security headers** — CSP, X-Frame-Options and more

### Requirements

- Docker + Docker Compose
- SSH access to the host (password or key)
- `sudo` access for `./start.sh` (or configured `NOPASSWD`)

### Setup

```bash
git clone https://github.com/darkvisa178/Minecraft-Server-dashboard.git
cd Minecraft-Server-dashboard

# Create .env with passwords (see .env.example)
cp .env.example .env
# Edit .env — set SSH_PASSWORD, CONSOLE_PASSWORD, etc.

# Create directory for SSH key
mkdir -p ssh

# Run
docker compose up -d --build
```

On first run the container generates an ED25519 key and automatically deploys it to `~/.ssh/authorized_keys` on the host (using the password from `.env`).  
After that `sshpass` is no longer used.

### Environment variables (.env)

| Variable | Description | Default |
|---|---|---|
| `SSH_PASSWORD` | SSH (and sudo) password | — |
| `CONSOLE_PASSWORD` | Web console login password | = SSH_PASSWORD |
| `RCON_PASSWORD` | RCON password | = SSH_PASSWORD |
| `SECRET_KEY` | Flask session key | random |
| `HOST_IP` | Host IP from container | `172.17.0.1` |
| `SSH_USER` | SSH user | `user` |
| `MINECRAFT_DIR` | Server directory | `/home/user/minecraft` |

### File structure

```
├── app.py                 # Flask server
├── Dockerfile             # Container image
├── docker-compose.yml     # Docker Compose
├── templates/index.html   # Web UI (SPA)
├── ssh/                   # SSH keys (gitignored)
├── .env                   # Secrets (gitignored)
└── .env.example           # Example config
```

---

## 🔒 Security

- SSH key authentication replaces password-based `sshpass` after first bootstrap
- Rate limiting: 5 failed login attempts per 60 seconds → HTTP 429
- Session timeout: 30 minutes of inactivity
- Security headers: CSP, X-Frame-Options, X-Content-Type-Options, X-XSS-Protection, Referrer-Policy
- Port 4444 binds to `127.0.0.1` only (use nginx reverse proxy for HTTPS)
- Secrets stored in `.env` (gitignored), never in code
