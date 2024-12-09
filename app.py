from flask import Flask, render_template, request, jsonify, send_from_directory, redirect, url_for, Response, after_this_request
import os
from werkzeug.utils import secure_filename
from functools import wraps
from datetime import datetime
import subprocess
import json
import os.path
import unicodedata
import re
from flask_sqlalchemy import SQLAlchemy
import sqlite3
from config import VIDEO_STATUS, MAX_CONCURRENT_TASKS
from tasks import compression_queue, redis_conn
import psutil

app = Flask(__name__, static_url_path='/static')
app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['MAX_CONTENT_LENGTH'] = 100 * 1024 * 1024  # 增加到100MB，但會在代碼中進行額外檢查
app.config['ADMIN_PASSWORD'] = 'admin123'  # 在實際環境中應使用更安全的方式存儲
ALLOWED_EXTENSIONS = {'mp4', 'mov', 'avi'}
MAX_FILE_SIZE = 100 * 1024 * 1024  # 16MB 限制

# 配置 SQLite 數據庫
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///videos.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

# 定義數據模型
class Video(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    filename = db.Column(db.String(255), unique=True, nullable=False)
    title = db.Column(db.String(255))
    votes = db.Column(db.Integer, default=0)
    views = db.Column(db.Integer, default=0)
    status = db.Column(db.String(20), default='uploaded')
    in_use = db.Column(db.Boolean, default=False)
    original_path = db.Column(db.String(255))
    compressed_path = db.Column(db.String(255))
    upload_time = db.Column(db.DateTime, default=datetime.utcnow)

class Vote(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    video_id = db.Column(db.Integer, db.ForeignKey('video.id'))
    ip_address = db.Column(db.String(50))
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)

# 確保上傳目錄存在
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

# 修改影片數據結構
videos = {
    # 'filename': {
    #     'votes': 0,
    #     'views': 0,
    #     'voters': set(),
    #     'status': 'uploaded',
    #     'in_use': False,
    #     'original_path': '',
    #     'compressed_path': ''
    # }
}

# 添加日期格式化過濾器
@app.template_filter('datetime')
def format_datetime(value):
    if isinstance(value, datetime):
        return value.strftime('%Y-%m-%d %H:%M:%S')
    return datetime.fromtimestamp(value).strftime('%Y-%m-%d %H:%M:%S')

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
            print("沒有證信息或認證格式錯誤")  # 調試日誌
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
    # 從數據庫獲取影片列表
    videos = Video.query.order_by(Video.upload_time.desc()).all()
    video_stats = [{
        'filename': video.filename,
        'votes': video.votes,
        'views': video.views,
        'status': video.status,
        'upload_time': video.upload_time
    } for video in videos]
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
        # 保存原始文件
        original_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        file.save(original_path)
        
        # 創建新的影片記錄
        video = Video(
            filename=filename,
            title=os.path.splitext(filename)[0],
            status=VIDEO_STATUS['UPLOADED'],  # 初始狀態為已上傳
            original_path=original_path,
            compressed_path=os.path.join(app.config['UPLOAD_FOLDER'], 'compressed_' + filename)
        )
        db.session.add(video)
        db.session.commit()
        
        # 將壓縮任務加入佇列
        compression_queue.enqueue(
            'tasks.compress_video_task',
            filename,
            job_timeout='1h'
        )
        
        return jsonify({'success': True, 'filename': filename})
        
    except Exception as e:
        db.session.rollback()
        if os.path.exists(original_path):
            os.remove(original_path)
        app.logger.error(f"上傳文件時出錯: {str(e)}")
        return jsonify({'error': f'文件上傳失敗: {str(e)}'}), 500

# 異步壓縮處理
def compress_video_async(filename):
    video = Video.query.filter_by(filename=filename).first()
    if not video:
        return
        
    try:
        # 更新狀態為壓縮中
        video.status = VIDEO_STATUS['COMPRESSING']
        db.session.commit()
        
        # 進行壓縮
        if compress_video(video.original_path, video.compressed_path):
            # 更新狀態為壓縮完成
            video.status = VIDEO_STATUS['COMPRESSED']
            db.session.commit()
            
            # 如果沒有人在看，直接替換文件
            if not video.in_use:
                replace_with_compressed(filename)
    except Exception as e:
        app.logger.error(f"壓縮失敗: {str(e)}")
        video.status = VIDEO_STATUS['UPLOADED']
        db.session.commit()

# 修改替換文件函數
def replace_with_compressed(filename):
    video = Video.query.filter_by(filename=filename).first()
    if not video or video.status != VIDEO_STATUS['COMPRESSED']:
        return
        
    try:
        os.replace(video.compressed_path, video.original_path)
        video.compressed_path = ''
        db.session.commit()
    except Exception as e:
        app.logger.error(f"替換文件失敗: {str(e)}")

# 添加一個字典來追蹤每個 IP 的投票狀態
user_votes = {}  # 格式: {ip: voted_filename}

@app.route('/vote/<filename>', methods=['POST'])
def vote(filename):
    user_ip = request.remote_addr
    video = Video.query.filter_by(filename=filename).first()
    
    if not video:
        return jsonify({'error': 'Video not found'}), 404
    
    # 檢查是否已投票
    existing_vote = Vote.query.filter_by(
        ip_address=user_ip
    ).first()
    
    if existing_vote and existing_vote.video_id == video.id:
        # 收回投票
        db.session.delete(existing_vote)
        video.votes -= 1
        voted = False
    else:
        # 如果之前投給其他影片，先收回
        if existing_vote:
            prev_video = Video.query.get(existing_vote.video_id)
            if prev_video:
                prev_video.votes -= 1
            db.session.delete(existing_vote)
        
        # 新增投票
        new_vote = Vote(video_id=video.id, ip_address=user_ip)
        db.session.add(new_vote)
        video.votes += 1
        voted = True
    
    db.session.commit()
    
    return jsonify({
        'votes': video.votes,
        'voted': voted,
        'previousVote': existing_vote.video_id if existing_vote else None
    })

@app.route('/view/<filename>', methods=['POST'])
def record_view(filename):
    if filename not in videos:
        videos[filename] = {'votes': 0, 'views': 0}
    videos[filename]['views'] += 1
    return jsonify({'views': videos[filename]['views']})

@app.route('/uploads/<filename>')
def uploaded_file(filename):
    video = Video.query.filter_by(filename=filename).first()
    if not video:
        return 'File not found', 404
        
    video.in_use = True
    db.session.commit()
    
    @after_this_request
    def after_request(response):
        video.in_use = False
        db.session.commit()
        # 如果壓縮完成且沒有其他人在看，進行替換
        if (video.status == VIDEO_STATUS['COMPRESSED'] and 
            not video.in_use and 
            video.compressed_path):
            replace_with_compressed(filename)
        return response
    
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

@app.route('/check_upload_status')
def check_upload_status():
    return jsonify({'has_uploaded': False})

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

@app.route('/videos')
def get_videos():
    user_ip = request.remote_addr
    videos = Video.query.order_by(Video.upload_time).all()
    
    # 獲取當前用戶的投票
    current_vote = Vote.query.filter_by(ip_address=user_ip).first()
    
    return jsonify([{
        'filename': video.filename,
        'title': video.title,
        'votes': video.votes,
        'views': video.views,
        'status': video.status,
        'voted': current_vote and current_vote.video_id == video.id
    } for video in videos])

@app.route('/compression_status')
@requires_auth
def compression_status():
    # 獲取所有任務狀態
    queued_jobs = compression_queue.get_jobs()  # 等待中的任務
    started_jobs = compression_queue.started_job_registry.get_job_ids()  # 正在執行的任務
    
    # 獲取 CPU 使用率
    cpu_percent = psutil.cpu_percent()
    
    status_data = {
        'queued': [job.args[0] for job in queued_jobs],  # 獲取文件名
        'processing': [
            Video.query.filter_by(filename=job_id.split(':')[-1]).first().filename 
            for job_id in started_jobs
        ],
        'cpu_usage': cpu_percent,
        'max_concurrent': MAX_CONCURRENT_TASKS
    }
    
    return jsonify(status_data)

# 初始化數據庫
with app.app_context():
    db.create_all()

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
