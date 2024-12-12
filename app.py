from flask import Flask, render_template, request, jsonify, send_from_directory, redirect, url_for, Response, after_this_request, session
import os
from werkzeug.utils import secure_filename
from functools import wraps
from datetime import datetime
import subprocess
import json
import os.path
import unicodedata
import re
from extensions import db, compression_queue, init_redis
from config import VIDEO_STATUS, MAX_CONCURRENT_TASKS
import psutil
from models import Video, Vote, View

app = Flask(__name__, static_url_path='/static')
app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['MAX_CONTENT_LENGTH'] = 100 * 1024 * 1024  # 增加到100MB，但會在代碼中進行額外檢查
app.config['ADMIN_PASSWORD'] = 'admin123'  # 在實際環境中應使用更安全的方式存儲
ALLOWED_EXTENSIONS = {'mp4', 'mov', 'avi'}
MAX_FILE_SIZE = 100 * 1024 * 1024  # 16MB 限制

# 配置 SQLite 數據庫
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///videos.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db.init_app(app)
init_redis()  # 初始化 Redis 連接

# 確保上傳目錄存在
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

# 添加 session 密鑰
app.secret_key = os.urandom(24)

# 改影片數據結構

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
        # 檢查 session 中是否已認證
        if session.get('authenticated'):
            return f(*args, **kwargs)
            
        auth = request.authorization
        if not auth:
            return authenticate()
            
        if not check_auth(auth.username, auth.password):
            return authenticate()
            
        # 認證成功，保存到 session
        session['authenticated'] = True
        return f(*args, **kwargs)
    return decorated

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/admin')
@requires_auth
def admin():
    # 定義狀態映射
    STATUS_MAP = {
        'uploaded': '已上傳',
        'compressing': '壓縮中',
        'completed': '已壓縮',
        'error': '處理失敗'
    }
    
    # 從數據庫獲取影片列表
    videos = Video.query.order_by(Video.upload_time.desc()).all()
    video_stats = [{
        'filename': video.filename,
        'votes': video.votes,
        'views': video.views,
        'status': video.status,
        'upload_time': video.upload_time
    } for video in videos]
    
    # 將 STATUS_MAP 傳遞給模板
    return render_template('admin.html', videos=video_stats, STATUS_MAP=STATUS_MAP)

@app.route('/delete/<filename>', methods=['POST'])
@requires_auth
def delete_video(filename):
    try:
        # 先從數據庫中獲取影片記錄
        video = Video.query.filter_by(filename=filename).first()
        if not video:
            return jsonify({'error': '影片記錄不存在'}), 404

        # 嘗試刪除實體文件
        file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        if os.path.exists(file_path):
            os.remove(file_path)
        
        # 如果有壓縮文件，也要刪除
        if video.compressed_path and os.path.exists(video.compressed_path):
            os.remove(video.compressed_path)
        
        # 刪除相關的投票錄
        Vote.query.filter_by(video_id=video.id).delete()
        
        # 從數據庫中刪除影片記錄
        db.session.delete(video)
        db.session.commit()
        
        return jsonify({'success': True})
        
    except Exception as e:
        db.session.rollback()
        app.logger.error(f"刪除影片失敗: {str(e)}")
        return jsonify({'error': f'刪除失敗: {str(e)}'}), 500

# 在文件頂部添加 FFmpeg 相關配置
FFMPEG_PARAMS = [
    '-vcodec', 'libx264',        # 使用 H.264 編碼
    '-crf', '28',                # 壓縮質量（18-28 之間，數字越大壓縮率越高）
    '-preset', 'medium',         # 壓縮速度（可選：ultrafast, superfast, veryfast, faster, fast, medium, slow, slower, veryslow
    '-acodec', 'aac',            # 音頻編碼
    '-ar', '44100',              # 音頻採樣率
    '-b:a', '128k',              # 音頻比特率
    '-movflags', '+faststart'    # 支持網頁快速播放
]

def compress_video(input_path, output_path):
    try:
        if not os.path.exists(input_path):
            app.logger.error(f"輸入文件不存在: {input_path}")
            return False
            
        ffmpeg_path = 'ffmpeg'
        command = [ffmpeg_path, '-i', input_path] + FFMPEG_PARAMS + [output_path]
        
        app.logger.info(f"執行 FFmpeg 命令: {' '.join(command)}")
        result = subprocess.run(command, capture_output=True, text=True)
        
        if result.returncode == 0:
            app.logger.info("FFmpeg 執行成功")
            return True
        else:
            app.logger.error(f"FFmpeg 執行失敗: {result.stderr}")
            return False
            
    except Exception as e:
        app.logger.error(f"壓縮過程發生錯誤: {str(e)}")
        return False

def secure_chinese_filename(filename):
    """自定義的安全檔��函數，支持中文"""
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
        
    custom_filename = request.form.get('custom_filename', '').strip()
    if not custom_filename:
        return jsonify({'error': '請輸入檔案名稱'}), 400
        
    original_extension = file.filename.rsplit('.', 1)[1].lower() if '.' in file.filename else ''
    if original_extension not in ALLOWED_EXTENSIONS:
        return jsonify({'error': '不支持的文件類型'}), 400
        
    filename = secure_chinese_filename(f"{custom_filename}.{original_extension}")
    filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    
    if os.path.exists(filepath):
        return jsonify({'error': '檔案名稱已存在，請使用其他名稱'}), 400
    
    try:
        # 保存文件
        file.save(filepath)
        
        # 創建影片記錄
        video = Video(
            filename=filename,
            title=custom_filename,
            status=VIDEO_STATUS['UPLOADED']
        )
        db.session.add(video)
        db.session.commit()
        
        # 立即開始壓縮處理
        compress_video_async(filename)
        
        return jsonify({'success': True, 'filename': filename})
        
    except Exception as e:
        db.session.rollback()
        if os.path.exists(filepath):
            os.remove(filepath)
        app.logger.error(f"上傳文件時出錯: {str(e)}")
        return jsonify({'error': f'文件上傳失敗: {str(e)}'}), 500

# 異步壓縮處理
def compress_video_async(filename):
    try:
        video = Video.query.filter_by(filename=filename).first()
        if not video:
            app.logger.error(f"找不到影片記錄: {filename}")
            return
            
        # 更新狀態為壓縮中
        video.status = VIDEO_STATUS['COMPRESSING']
        db.session.commit()
        
        # 設置壓縮文件路徑
        input_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        output_path = os.path.join(app.config['UPLOAD_FOLDER'], f'compressed_{filename}')
        
        app.logger.info(f"開始壓縮影片: {filename}")
        app.logger.info(f"輸入路徑: {input_path}")
        app.logger.info(f"輸出路徑: {output_path}")
        
        # 進行壓縮
        if compress_video(input_path, output_path):
            app.logger.info(f"影片壓縮成功: {filename}")
            # 直接替換原始文件
            try:
                os.replace(output_path, input_path)
                video.status = VIDEO_STATUS['COMPLETED']
                db.session.commit()
                app.logger.info(f"影片處理完成: {filename}")
            except Exception as e:
                app.logger.error(f"替換文件失敗: {str(e)}")
                video.status = VIDEO_STATUS['ERROR']
                db.session.commit()
        else:
            app.logger.error(f"影片壓縮失敗: {filename}")
            video.status = VIDEO_STATUS['ERROR']
            db.session.commit()
            
    except Exception as e:
        app.logger.error(f"壓縮處理過程發生錯誤: {str(e)}")
        if video:
            video.status = VIDEO_STATUS['ERROR']
            db.session.commit()

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
        # 如果之前給其他影片，先收回
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
    video = Video.query.filter_by(filename=filename).first()
    if not video:
        return jsonify({'error': 'Video not found'}), 404
        
    user_ip = request.remote_addr
    
    # 檢查是否已經觀看過
    existing_view = View.query.filter_by(
        video_id=video.id,
        ip_address=user_ip
    ).first()
    
    if not existing_view:
        # 新增觀看記錄
        new_view = View(video_id=video.id, ip_address=user_ip)
        db.session.add(new_view)
        video.views += 1
        db.session.commit()
    
    return jsonify({'views': video.views})

@app.route('/uploads/<filename>')
def uploaded_file(filename):
    video = Video.query.filter_by(filename=filename).first()
    if not video:
        return 'File not found', 404
    
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
        'id': video.id,           # 添加 id
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
    # 如果沒有可用的壓縮隊列
    if not compression_queue:
        return jsonify({
            'queued': [],
            'processing': [],
            'cpu_usage': psutil.cpu_percent(),
            'max_concurrent': MAX_CONCURRENT_TASKS,
            'mode': 'sync'  # 表示在使用步模式
        })
    
    # 獲取所有任務狀態
    queued_jobs = compression_queue.get_jobs()  # 等待中的任務
    started_jobs = compression_queue.started_job_registry.get_job_ids()  # 正在執行的任務
    
    status_data = {
        'queued': [job.args[0] for job in queued_jobs],
        'processing': [
            Video.query.filter_by(filename=job_id.split(':')[-1]).first().filename 
            for job_id in started_jobs
        ],
        'cpu_usage': psutil.cpu_percent(),
        'max_concurrent': MAX_CONCURRENT_TASKS,
        'mode': 'queue'  # 表示正在使用隊列模式
    }
    
    return jsonify(status_data)

# 添加登出路由
@app.route('/logout')
def logout():
    session.pop('authenticated', None)
    return redirect(url_for('admin'))

# 初始化數據庫
with app.app_context():
    db.create_all()

@app.route('/rankings')
def get_rankings():
    # 獲取前5名得票最高的影片
    top_videos = Video.query.order_by(Video.votes.desc()).limit(5).all()
    
    return jsonify([{
        'title': video.title,
        'votes': video.votes,
        'filename': video.filename
    } for video in top_videos])

@app.route('/compression_status/<filename>')
def check_compression_status(filename):
    video = Video.query.filter_by(filename=filename).first()
    if not video:
        return jsonify({'status': 'error'})
    
    return jsonify({'status': video.status})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
