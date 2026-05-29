from flask import Flask, render_template, request, session, redirect, url_for, jsonify
from flask_socketio import SocketIO, emit, join_room, leave_room
import os, random, string, base64

app = Flask(__name__)
app.config['SECRET_KEY'] = 'drawapp_secret_2024!'
socketio = SocketIO(app, cors_allowed_origins="*", max_http_buffer_size=10 * 1024 * 1024)

users = {}       # sid -> user info
rooms = {}       # code -> room info
username_to_sid = {}  # username -> sid

AVATARS = ['🎨','🦊','🐱','🐼','🦁','🐸','🦄','🐙','🦋','🌟','🎭','🎪','🦩','🐬','🦚']

def gen_room_code():
    while True:
        code = ''.join(random.choices(string.digits, k=6))
        if code not in rooms:
            return code

def get_members_info(code):
    if code not in rooms:
        return []
    return [
        {
            'username': users[sid]['username'],
            'avatar': users[sid]['avatar'],
            'color': users[sid]['color'],
            'sid': sid
        }
        for sid in rooms[code]['members'] if sid in users
    ]

def cleanup_empty_rooms():
    empty = [c for c, r in rooms.items() if not r['members']]
    for c in empty:
        del rooms[c]

@app.route('/')
def index():
    if 'username' not in session:
        return redirect(url_for('login'))
    return render_template('index.html', username=session['username'], avatar=session.get('avatar','🎨'), color=session.get('color','#FF6B6B'))

@app.route('/login', methods=['GET','POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username','').strip()
        avatar = request.form.get('avatar','🎨')
        color = request.form.get('color','#FF6B6B')
        if not username or len(username) < 2:
            return render_template('login.html', error='الاسم قصير جداً', avatars=AVATARS)
        session['username'] = username
        session['avatar'] = avatar
        session['color'] = color
        return redirect(url_for('index'))
    return render_template('login.html', avatars=AVATARS, error=None)

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

# ─── Socket Events ───────────────────────────────────────────

@socketio.on('connect')
def on_connect():
    sid = request.sid
    username = session.get('username')
    avatar = session.get('avatar', '🎨')
    color = session.get('color', '#FF6B6B')
    if not username:
        return False
    users[sid] = {
        'username': username,
        'avatar': avatar,
        'color': color,
        'friends': [],
        'friend_requests': [],
        'room': None
    }
    username_to_sid[username] = sid

@socketio.on('disconnect')
def on_disconnect():
    sid = request.sid
    if sid not in users:
        return
    user = users[sid]
    code = user.get('room')
    if code and code in rooms:
        rooms[code]['members'] = [m for m in rooms[code]['members'] if m != sid]
        emit('user_left', {'username': user['username'], 'avatar': user['avatar']}, room=code)
        emit('room_members', {'members': get_members_info(code)}, room=code)
    username_to_sid.pop(user['username'], None)
    del users[sid]
    cleanup_empty_rooms()

@socketio.on('create_room')
def on_create_room():
    sid = request.sid
    if sid not in users:
        return
    code = gen_room_code()
    rooms[code] = {'members': [], 'history': [], 'max': 4}
    _join_room(sid, code)

@socketio.on('join_room_code')
def on_join_room(data):
    sid = request.sid
    if sid not in users:
        return
    code = str(data.get('code', '')).strip()
    if code not in rooms:
        emit('room_error', {'msg': 'الغرفة غير موجودة، تحقق من الرقم'})
        return
    if len(rooms[code]['members']) >= rooms[code]['max']:
        emit('room_error', {'msg': 'الغرفة ممتلئة (4 أشخاص)'})
        return
    _join_room(sid, code)

def _join_room(sid, code):
    user = users[sid]
    old_code = user.get('room')
    if old_code and old_code in rooms:
        rooms[old_code]['members'] = [m for m in rooms[old_code]['members'] if m != sid]
        leave_room(old_code)
        emit('room_members', {'members': get_members_info(old_code)}, room=old_code)
    rooms[code]['members'].append(sid)
    users[sid]['room'] = code
    join_room(code)
    emit('room_joined', {'code': code, 'history': rooms[code]['history']})
    emit('room_members', {'members': get_members_info(code)}, room=code)
    emit('user_joined', {'username': user['username'], 'avatar': user['avatar']}, room=code)

@socketio.on('draw')
def on_draw(data):
    sid = request.sid
    if sid not in users:
        return
    code = users[sid].get('room')
    if not code or code not in rooms:
        return
    data['username'] = users[sid]['username']
    data['color_user'] = users[sid]['color']
    data['type'] = 'draw'
    rooms[code]['history'].append(data)
    if len(rooms[code]['history']) > 2000:
        rooms[code]['history'] = rooms[code]['history'][-2000:]
    emit('draw', data, room=code, include_self=False)

@socketio.on('add_image')
def on_add_image(data):
    sid = request.sid
    if sid not in users:
        return
    code = users[sid].get('room')
    if not code or code not in rooms:
        return
    event = {'type': 'image', 'src': data['src'], 'x': data['x'], 'y': data['y'], 'w': data['w'], 'h': data['h']}
    rooms[code]['history'].append(event)
    emit('add_image', event, room=code, include_self=False)

@socketio.on('clear_canvas')
def on_clear():
    sid = request.sid
    if sid not in users:
        return
    code = users[sid].get('room')
    if code and code in rooms:
        rooms[code]['history'] = []
        emit('clear_canvas', room=code)

@socketio.on('chat')
def on_chat(data):
    sid = request.sid
    if sid not in users:
        return
    code = users[sid].get('room')
    if not code:
        return
    msg = str(data.get('msg', '')).strip()
    if not msg:
        return
    emit('chat', {
        'username': users[sid]['username'],
        'avatar': users[sid]['avatar'],
        'color': users[sid]['color'],
        'msg': msg
    }, room=code)

@socketio.on('send_friend_request')
def on_friend_request(data):
    sid = request.sid
    if sid not in users:
        return
    target = data.get('username', '').strip()
    sender = users[sid]
    if target == sender['username']:
        emit('friend_error', {'msg': 'لا تستطيع إضافة نفسك!'})
        return
    if target in sender['friends']:
        emit('friend_error', {'msg': 'هذا الشخص صديقك بالفعل'})
        return
    target_sid = username_to_sid.get(target)
    if not target_sid or target_sid not in users:
        emit('friend_error', {'msg': 'المستخدم غير متصل الآن'})
        return
    emit('friend_request_received', {
        'from': sender['username'],
        'avatar': sender['avatar'],
        'color': sender['color']
    }, room=target_sid)
    emit('friend_request_sent', {'to': target})

@socketio.on('accept_friend')
def on_accept_friend(data):
    sid = request.sid
    if sid not in users:
        return
    from_username = data.get('username', '').strip()
    user = users[sid]
    from_sid = username_to_sid.get(from_username)
    if not from_sid or from_sid not in users:
        return
    if from_username not in user['friends']:
        user['friends'].append(from_username)
    if user['username'] not in users[from_sid]['friends']:
        users[from_sid]['friends'].append(user['username'])
    emit('friend_added', {'username': from_username, 'avatar': users[from_sid]['avatar'], 'color': users[from_sid]['color']})
    emit('friend_added', {'username': user['username'], 'avatar': user['avatar'], 'color': user['color']}, room=from_sid)

@socketio.on('invite_friend')
def on_invite_friend(data):
    sid = request.sid
    if sid not in users:
        return
    target = data.get('username', '').strip()
    code = users[sid].get('room')
    if not code:
        emit('friend_error', {'msg': 'أنت لست في غرفة'})
        return
    target_sid = username_to_sid.get(target)
    if not target_sid:
        emit('friend_error', {'msg': 'الصديق غير متصل'})
        return
    emit('room_invite', {'from': users[sid]['username'], 'code': code}, room=target_sid)

if __name__ == '__main__':
    os.makedirs('templates', exist_ok=True)
    os.makedirs('static', exist_ok=True)
    port = int(os.environ.get('PORT', 5000))
socketio.run(app, host='0.0.0.0', port=port, debug=False)
