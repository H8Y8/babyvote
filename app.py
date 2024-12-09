from flask import Flask, render_template, request, jsonify, send_from_directory, redirect, url_for, Response
import os
from werkzeug.utils import secure_filename
from functools import wraps
from datetime import datetime
import subprocess
import json
import os.path
import unicodedata
import re

app = Flask(__name__, static_url_path='/static')
app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['MAX_CONTENT_LENGTH'] = 100 * 1024 * 1024  # 增加到100MB，但會在代碼中進行額外檢查
app.config['ADMIN_PASSWORD'] = 'admin123'  # 在實際環境中應使用更安全的方式存儲
ALLOWED_EXTENSIONS = {'mp4', 'mov', 'avi'}
MAX_FILE_SIZE = 100 * 1024 * 1024  # 16MB 限制

# 確保上傳目錄存在
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

# 存儲影片數據
videos = {}

# 添加日期格式化過濾器
@app.template_filter('datetime')
def format_datetime(value):
    dt = datetime.fromtimestamp(value)
    return dt.strftime('%Y-%m-%d %H:%M:%S')

def check_auth(username, password):
    """檢查用戶名和密碼是否正確"""
    return username == 'admin' and password == app.config['ADMIN_PASSWORD']

def authenticate():
    """發送 401 響應，觸發基本認證對話框"""
    return Response(
        'Login required',
        401,
        {'WWW-Authenticate': 'Basic realm="Login Required"', 'Content-Type': 'text/plain'}
    )

def requires_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        print("認證開始")  # 調試日誌
        auth = request.authorization
        
        if not auth:
            print("沒有認證信息或認證格式錯誤")  # 調試日誌
            return authenticate()
            
        print(f"收到認證請求 - 用戶名: {auth.username}")  # 調試日誌
        
        if not auth.username or not auth.password:
            print("用戶名或密碼為空")  # 調試日誌
            return authenticate()
            
        if auth.username != 'admin':
            print(f"用戶名錯誤: {auth.username}")  # 調試日誌
            return authenticate()
            
        if auth.password != app.config['ADMIN_PASSWORD']:
            print("密碼錯誤")  # 調試日誌
            return authenticate()
            
        print("認證成功")  # 調試日誌
        return f(*args, **kwargs)
    return decorated

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/admin')
@requires_auth
def admin():
    video_stats = []
    for filename in os.listdir(app.config['UPLOAD_FOLDER']):
        if allowed_file(filename):
            stats = videos.get(filename, {'votes': 0, 'views': 0})
            video_stats.append({
                'filename': filename,
                'votes': stats.get('votes', 0),
                'views': stats.get('views', 0),
                'upload_time': os.path.getctime(os.path.join(app.config['UPLOAD_FOLDER'], filename))
            })
    video_stats.sort(key=lambda x: x['upload_time'], reverse=True)
    return render_template('admin.html', videos=video_stats)

@app.route('/delete/<filename>', methods=['POST'])
@requires_auth
def delete_video(filename):
    try:
        file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        if os.path.exists(file_path):
            os.remove(file_path)
            if filename in videos:
                del videos[filename]
            return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    return jsonify({'error': 'File not found'}), 404

# 在文件頂部添加 FFmpeg 相關配置
FFMPEG_PARAMS = [
    '-vcodec', 'libx264',        # 使用 H.264 編碼
    '-crf', '28',                # 壓縮質量（18-28 之間，數字越大壓縮率越高）
    '-preset', 'medium',         # 壓縮速度（可選：ultrafast, superfast, veryfast, faster, fast, medium, slow, slower, veryslow）
    '-acodec', 'aac',            # 音頻編碼
    '-ar', '44100',              # 音頻採樣率
    '-b:a', '128k',              # 音頻比特率
    '-movflags', '+faststart'    # 支持網頁快速播放
]

def compress_video(input_path, output_path):
    """壓縮影片"""
    try:
        # 確保輸出路徑包含副檔名
        if not output_path.lower().endswith(('.mp4', '.mov', '.avi')):
            output_path += '.mp4'  # 預設使用 mp4 格式
            
        ffmpeg_path = 'ffmpeg'
        command = [ffmpeg_path, '-i', input_path] + FFMPEG_PARAMS + [output_path]
        
        # 添加錯誤日誌
        result = subprocess.run(command, check=True, capture_output=True, text=True)
        app.logger.info(f"FFmpeg 執行成功: {result.stdout}")
        return True
        
    except subprocess.CalledProcessError as e:
        app.logger.error(f"影片壓縮失敗: {e.stderr}")
        return False

def secure_chinese_filename(filename):
    """自定義的安全檔名函數，支持中文"""
    # 將檔名分成名稱和副檔名
    name, ext = os.path.splitext(filename)
    
    # 移除控制字符
    name = "".join(ch for ch in name if unicodedata.category(ch)[0] != "C")
    
    # 替換特殊字符為底線
    name = re.sub(r'[<>:"/\\|?*]', '_', name)
    
    # 確保檔名不為空
    if not name:
        name = '_'
    
    return name + ext

# 修改上傳路由
@app.route('/upload', methods=['POST'])
def upload_file():
    if 'video' not in request.files:
        return jsonify({'error': '沒有影片文件'}), 400
    
    file = request.files['video']
    if file.filename == '':
        return jsonify({'error': '沒有選擇文件'}), 400
    
    # 獲取用戶自定義的文件名
    custom_filename = request.form.get('custom_filename', '').strip()
    if not custom_filename:
        return jsonify({'error': '請輸入檔案名稱'}), 400
        
    # 獲取原始文件的副檔名
    original_extension = file.filename.rsplit('.', 1)[1].lower() if '.' in file.filename else ''
    if original_extension not in ALLOWED_EXTENSIONS:
        return jsonify({'error': '不支持的文件類型'}), 400
        
    # 組合新的文件名（自定義名稱 + 原始副檔名）
    filename = secure_chinese_filename(f"{custom_filename}.{original_extension}")
    
    # 檢查文件名是否已存在
    filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    if os.path.exists(filepath):
        return jsonify({'error': '檔案名稱已存在，請使用其他名稱'}), 400
    
    # 檢查文件大小
    file.seek(0, os.SEEK_END)
    file_size = file.tell()
    file.seek(0)  # 重置文件指針到開始位置
    
    if file_size > MAX_FILE_SIZE:
        return jsonify({'error': f'文件大小超過限制（最大 {MAX_FILE_SIZE // (1024*1024)}MB）'}), 400
    
    try:
        # 先保存原始文件到臨時目錄
        temp_filepath = os.path.join(app.config['UPLOAD_FOLDER'], 'temp_' + filename)
        file.save(temp_filepath)
        
        # 壓縮後的文件路徑
        compressed_filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        
        # 進行壓縮
        if compress_video(temp_filepath, compressed_filepath):
            try:
                # 刪除臨時文件
                os.remove(temp_filepath)
                
                # 檢查壓縮後的文件大小
                if os.path.getsize(compressed_filepath) > MAX_FILE_SIZE:
                    os.remove(compressed_filepath)
                    return jsonify({'error': '壓縮後文件仍然過大'}), 400
                
                # 初始化影片數據
                videos[filename] = {'votes': 0, 'views': 0, 'voters': set()}
                return jsonify({'success': True, 'filename': filename})
                
            except Exception as e:
                # 如果在處理過程中出錯，清理文件
                if os.path.exists(compressed_filepath):
                    os.remove(compressed_filepath)
                app.logger.error(f"處理壓縮文件時出錯: {str(e)}")
                return jsonify({'error': '處理文件時發生錯誤'}), 500
        else:
            # 壓縮失敗，清理臨時文件
            if os.path.exists(temp_filepath):
                os.remove(temp_filepath)
            return jsonify({'error': '影片壓縮失敗'}), 500
            
    except Exception as e:
        # 清理所有臨時文件
        if os.path.exists(temp_filepath):
            os.remove(temp_filepath)
        if os.path.exists(compressed_filepath):
            os.remove(compressed_filepath)
        app.logger.error(f"上傳文件時出錯: {str(e)}")
        return jsonify({'error': f'文件上傳失敗: {str(e)}'}), 500

# 添加一個字典來追蹤每個 IP 的投票狀態
user_votes = {}  # 格式: {ip: voted_filename}

@app.route('/vote/<filename>', methods=['POST'])
def vote(filename):
    user_ip = request.remote_addr
    
    if filename not in videos:
        videos[filename] = {'votes': 0, 'views': 0, 'voters': set()}
    
    # 檢查用戶是否已經投過票給其他影片
    previously_voted_file = user_votes.get(user_ip)
    
    # 如果用戶點擊的是已投票的影片，收回票
    if previously_voted_file == filename:
        videos[filename]['voters'].remove(user_ip)
        videos[filename]['votes'] -= 1
        user_votes.pop(user_ip)
        voted = False
    else:
        # 如果用戶之前投給了其他影片，先收回那個票
        if previously_voted_file:
            videos[previously_voted_file]['voters'].remove(user_ip)
            videos[previously_voted_file]['votes'] -= 1
        
        # 投票給新的影片
        videos[filename]['voters'].add(user_ip)
        videos[filename]['votes'] += 1
        user_votes[user_ip] = filename
        voted = True
    
    return jsonify({
        'votes': videos[filename]['votes'],
        'voted': voted,
        'previousVote': previously_voted_file
    })

@app.route('/view/<filename>', methods=['POST'])
def record_view(filename):
    if filename not in videos:
        videos[filename] = {'votes': 0, 'views': 0}
    videos[filename]['views'] += 1
    return jsonify({'views': videos[filename]['views']})

@app.route('/uploads/<filename>')
def uploaded_file(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

@app.route('/check_upload_status')
def check_upload_status():
    return jsonify({'has_uploaded': False})

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

@app.route('/videos')
def get_videos():
    user_ip = request.remote_addr
    video_files = []
    
    # 獲取當前用戶投票的影片
    current_vote = user_votes.get(user_ip)
    
    files_with_time = []
    for filename in os.listdir(app.config['UPLOAD_FOLDER']):
        if allowed_file(filename):
            file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            creation_time = os.path.getctime(file_path)
            files_with_time.append((filename, creation_time))
    
    files_with_time.sort(key=lambda x: x[1])
    
    for filename, _ in files_with_time:
        stats = videos.get(filename, {'votes': 0, 'views': 0, 'voters': set()})
        title = os.path.splitext(filename)[0]
        video_files.append({
            'filename': filename,
            'title': title,
            'votes': stats.get('votes', 0),
            'views': stats.get('views', 0),
            'voted': filename == current_vote  # 檢查是否是當前用戶投票的影片
        })
    
    return jsonify(video_files)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
