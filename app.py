import os
import socket
import struct
import subprocess
import threading
import time
import re
import secrets
from datetime import timedelta
from functools import wraps
from flask import Flask, Response, render_template, jsonify, request, session
from werkzeug.middleware.proxy_fix import ProxyFix

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', secrets.token_hex(32))
app.permanent_session_lifetime = timedelta(minutes=30)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1)

SSH_KEY_PATH = '/ssh/id_ed25519'
login_attempts = {}
login_lock = threading.Lock()

HOST_IP = os.environ.get('HOST_IP', '172.17.0.1')
SSH_USER = os.environ.get('SSH_USER', 'user')
SSH_PASSWORD = os.environ.get('SSH_PASSWORD', '')
CONSOLE_PASSWORD = os.environ.get('CONSOLE_PASSWORD', SSH_PASSWORD)
MINECRAFT_DIR = os.environ.get('MINECRAFT_DIR', '/home/user/minecraft')
MINECRAFT_PORT = int(os.environ.get('MINECRAFT_PORT', '25565'))
RCON_PORT = int(os.environ.get('RCON_PORT', '25575'))
RCON_PASSWORD = os.environ.get('RCON_PASSWORD', SSH_PASSWORD)

starting = False
AUTO_STOP_DELAY = 900

player_state = {}
player_lock = threading.Lock()

JOIN_RE = re.compile(r'(\w+) joined the game')
LEAVE_RE = re.compile(r'(\w+) left the game')
LIST_RE = re.compile(r'There are (\d+) of a max of \d+ players online: (.*)')


def reset_player_state():
    global player_state
    with player_lock:
        player_state = {'names': set(), 'last_change': time.time(), 'warned': False}


reset_player_state()


def require_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('authenticated'):
            return jsonify({'error': 'unauthorized'}), 401
        return f(*args, **kwargs)
    return decorated


def ssh_pass(cmd):
    return (
        f"sshpass -p '{SSH_PASSWORD}' ssh -o StrictHostKeyChecking=no "
        f"-o UserKnownHostsFile=/dev/null {SSH_USER}@{HOST_IP} "
        f"\"{cmd}\""
    )


def ssh(cmd):
    if os.path.exists(SSH_KEY_PATH):
        return (
            f"ssh -i {SSH_KEY_PATH} -o StrictHostKeyChecking=no "
            f"-o UserKnownHostsFile=/dev/null {SSH_USER}@{HOST_IP} "
            f"\"{cmd}\""
        )
    return ssh_pass(cmd)


def bootstrap_ssh_key():
    if os.path.exists(SSH_KEY_PATH):
        return True
    os.makedirs(os.path.dirname(SSH_KEY_PATH), exist_ok=True)
    r = subprocess.run(['ssh-keygen', '-t', 'ed25519', '-f', SSH_KEY_PATH, '-N', ''],
                       capture_output=True, timeout=10)
    if r.returncode != 0:
        return False
    pubkey = open(SSH_KEY_PATH + '.pub').read().strip()
    print(f"SSH public key: {pubkey}")
    add = f"mkdir -p ~/.ssh && echo '{pubkey}' >> ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys"
    r = subprocess.run(ssh_pass(add), shell=True, timeout=10, capture_output=True)
    if r.returncode == 0:
        return True
    print("SSH key deploy failed, falling back to password auth")
    return False


def check_port():
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(2)
    try:
        r = sock.connect_ex((HOST_IP, MINECRAFT_PORT))
        sock.close()
        return r == 0
    except:
        return False


def _recv_exact(sock, n):
    buf = b''
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError
        buf += chunk
    return buf


def rcon_send(cmd_text):
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(5)
        s.connect((HOST_IP, RCON_PORT))
        rid = int.from_bytes(os.urandom(4), 'big') & 0x7fffffff
        login = struct.pack('<ii', rid, 3) + RCON_PASSWORD.encode() + b'\0\0'
        s.sendall(struct.pack('<i', len(login)) + login)
        _recv_exact(s, 4)
        _recv_exact(s, 8)
        payload = cmd_text.encode() + b'\0'
        cmd = struct.pack('<ii', rid, 2) + payload + b'\0'
        s.sendall(struct.pack('<i', len(cmd)) + cmd)
        resp_len = struct.unpack('<i', _recv_exact(s, 4))[0]
        _recv_exact(s, resp_len)
        s.close()
        return True
    except:
        return False


def send_console(cmd_text):
    safe = cmd_text.replace("'", "'\\''")
    cmd = ssh(f"tmux send-keys -t mc '{safe}' Enter")
    r = subprocess.run(cmd, shell=True, timeout=10, capture_output=True)
    if r.returncode != 0:
        rcon_send(cmd_text)


def process_log_line(text):
    with player_lock:
        join_m = JOIN_RE.search(text)
        leave_m = LEAVE_RE.search(text)
        list_m = LIST_RE.search(text)

        if join_m:
            player_state['names'].add(join_m.group(1))
            player_state['last_change'] = time.time()
            player_state['warned'] = False
        elif leave_m:
            player_state['names'].discard(leave_m.group(1))
            player_state['last_change'] = time.time()
            player_state['warned'] = False
        elif list_m:
            names_str = list_m.group(2).strip()
            if names_str:
                player_state['names'] = set(n.strip() for n in names_str.split(',') if n.strip())
            else:
                player_state['names'] = set()
            player_state['last_change'] = time.time()


def player_tracker_loop():
    while True:
        try:
            with player_lock:
                player_state['names'] = set()
                player_state['last_change'] = time.time()
                player_state['warned'] = False

            hist = subprocess.run(
                ssh(f"cat {MINECRAFT_DIR}/logs/latest.log 2>/dev/null || true"),
                shell=True, capture_output=True, text=True, timeout=30
            )
            if hist.returncode == 0 and hist.stdout:
                for line in hist.stdout.splitlines():
                    process_log_line(line)

            with player_lock:
                player_state['last_change'] = time.time()

            cmd = ssh(f"tail -n 0 -F {MINECRAFT_DIR}/logs/latest.log")
            p = subprocess.Popen(
                cmd, shell=True,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, bufsize=1
            )

            for raw in iter(p.stdout.readline, b''):
                if not raw:
                    break
                process_log_line(raw.decode(errors='replace').rstrip())

            p.terminate()
            p.wait()
        except:
            pass
        time.sleep(5)


def auto_shutdown_loop():
    while True:
        time.sleep(30)
        try:
            if not check_port():
                continue

            with player_lock:
                pcount = len(player_state['names'])
                idle = time.time() - player_state['last_change']
                warned = player_state['warned']

            if pcount > 0:
                continue

            if idle >= AUTO_STOP_DELAY - 30 and not warned:
                send_console('say §c⚠ Server will shutdown in 30 seconds due to inactivity!')
                with player_lock:
                    player_state['warned'] = True

            if idle >= AUTO_STOP_DELAY:
                send_console('say §c⚠ Server shutting down now!')
                time.sleep(3)
                subprocess.run(ssh("tmux send-keys -t mc '/stop' Enter"), shell=True, timeout=10)
                time.sleep(5)
                subprocess.run(ssh("tmux kill-session -t mc"), shell=True, timeout=10)
                reset_player_state()
        except:
            pass


threading.Thread(target=player_tracker_loop, daemon=True).start()
threading.Thread(target=auto_shutdown_loop, daemon=True).start()
threading.Thread(target=bootstrap_ssh_key, daemon=True).start()


@app.after_request
def add_security_headers(response):
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['X-XSS-Protection'] = '1; mode=block'
    response.headers['Referrer-Policy'] = 'same-origin'
    response.headers['Content-Security-Policy'] = (
        "default-src 'self'; "
        "style-src 'self' 'unsafe-inline'; "
        "script-src 'self' 'unsafe-inline'; "
        "connect-src 'self'; "
        "img-src 'self' data:; "
        "font-src 'self' data:"
    )
    return response


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/api/status')
def get_status():
    global starting
    if check_port():
        starting = False
        return jsonify({'status': 'running'})
    if starting:
        return jsonify({'status': 'starting'})
    return jsonify({'status': 'stopped'})


@app.route('/api/players')
def get_players():
    running = check_port()
    with player_lock:
        names = sorted(player_state['names'])
        count = len(names)
        if running and count == 0:
            idle = int(time.time() - player_state['last_change'])
            auto_stop = max(0, int(AUTO_STOP_DELAY - idle))
        else:
            idle = 0
            auto_stop = None

    return jsonify({
        'count': count,
        'names': names,
        'idle_seconds': idle,
        'auto_stop_seconds': auto_stop,
    })


@app.route('/api/login', methods=['POST'])
def login():
    ip = request.remote_addr
    now = time.time()
    with login_lock:
        attempts = login_attempts.get(ip, [])
        attempts = [t for t in attempts if now - t < 60]
        if len(attempts) >= 5:
            return jsonify({'error': 'too many attempts'}), 429
        login_attempts[ip] = attempts

    data = request.get_json()
    if data and data.get('password') == CONSOLE_PASSWORD:
        with login_lock:
            login_attempts.pop(ip, None)
        session['authenticated'] = True
        session.permanent = True
        return jsonify({'ok': True})

    with login_lock:
        login_attempts.setdefault(ip, []).append(now)
    return jsonify({'error': 'wrong password'}), 403


@app.route('/api/logout', methods=['POST'])
def logout():
    session.pop('authenticated', None)
    return jsonify({'ok': True})


@app.route('/api/check-auth')
def check_auth():
    return jsonify({'authenticated': session.get('authenticated', False)})


@app.route('/api/start', methods=['POST'])
def start_server():
    global starting
    if starting:
        return jsonify({'error': 'already starting'}), 409
    if check_port():
        return jsonify({'error': 'already running'}), 409

    starting = True

    def do_start():
        subprocess.run(ssh("tmux new-session -d -s mc"), shell=True, timeout=10)
        subprocess.run(ssh(f"tmux send-keys -t mc 'cd {MINECRAFT_DIR} && sudo -S ./start.sh' Enter"), shell=True, timeout=10)
        time.sleep(2)
        subprocess.run(ssh(f"tmux send-keys -t mc '{SSH_PASSWORD}' Enter"), shell=True, timeout=10)

    threading.Thread(target=do_start, daemon=True).start()
    return jsonify({'message': 'starting'})


@app.route('/api/stop', methods=['POST'])
@require_auth
def stop_server():
    global starting

    with player_lock:
        if len(player_state['names']) > 0:
            return jsonify({
                'error': f'Cannot stop: {len(player_state["names"])} player(s) online',
                'players': sorted(player_state['names'])
            }), 403

    starting = False

    def do_stop():
        subprocess.run(ssh("tmux send-keys -t mc '/stop' Enter"), shell=True, timeout=10)
        time.sleep(5)
        subprocess.run(ssh("tmux kill-session -t mc"), shell=True, timeout=10)
        reset_player_state()

    threading.Thread(target=do_stop, daemon=True).start()
    return jsonify({'message': 'stopping'})


@app.route('/api/command', methods=['POST'])
@require_auth
def send_command():
    data = request.get_json()
    if not data or not data.get('command'):
        return jsonify({'error': 'no command'}), 400
    cmd = data['command'].strip()
    if not cmd:
        return jsonify({'error': 'empty command'}), 400
    threading.Thread(target=lambda: send_console(cmd), daemon=True).start()
    return jsonify({'message': 'ok'})


@app.route('/api/log')
@require_auth
def stream_log():
    def gen():
        p = None
        try:
            p = subprocess.Popen(
                ssh(f"tail -n 100 -F {MINECRAFT_DIR}/logs/latest.log"),
                shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, bufsize=1
            )
            for line in iter(p.stdout.readline, b''):
                yield f"data: {line.decode(errors='replace').rstrip()}\n\n"
        except GeneratorExit:
            pass
        finally:
            if p:
                p.terminate()
                try:
                    p.wait(3)
                except:
                    p.kill()

    return Response(gen(), mimetype='text/event-stream',
                    headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False, threaded=True)
