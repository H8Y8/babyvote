from flask import Flask, render_template, request, jsonify, send_from_directory, redirect, url_for, Response, after_this_request
import os
from werkzeug.utils import secure_filename
from functools import wraps
from datetime import datetime
import subprocess
import os.path
import unicodedata
import re
from extensions import db, compression_queue, init_redis, redis_conn
from config import VIDEO_STATUS, MAX_CONCURRENT_TASKS
import psutil
from models import Video, Vote
from time import sleep
from redis.exceptions import ConnectionError
from rq import Worker

app = Flask(__name__, static_url_path='/static')
app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['MAX_CONTENT_LENGTH'] = 100 * 1024 * 1024  # 增加到100MB，但會在代碼中進行額外檢查
app.config['ADMIN_PASSWORD'] = 'admin123'  # 在實際環境中應使用更安全的方式存儲
ALLOWED_EXTENSIONS = {'mp4', 'mov', 'avi'}
MAX_FILE_SIZE = 100 * 1024 * 1024  # 16MB 限制

# 配置 SQLite 數據庫
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///instance/videos.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SQLALCHEMY_ECHO'] = True  # 添加 SQL 日誌
db.init_app(app)

# 確保 instance 目錄存在
os.makedirs('instance', exist_ok=True)

# 確保上傳目錄存在
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

# 初始化數據庫
with app.app_context():
    try:
        db.create_all()
        print("數據庫表創建成功")
    except Exception as e:
        print(f"數據庫初始化錯誤: {str(e)}")

# 在文件頂部添加
MAX_REDIS_RETRIES = 5
retry_count = 0
redis_connected = False

while retry_count < MAX_REDIS_RETRIES and not redis_connected:
    try:
        print(f"嘗試連接 Redis (第 {retry_count + 1} 次)")
        if init_redis():
            redis_connected = True
            print("Redis 連接成功")
            break
        print(f"Redis 連接失敗，重試中 ({retry_count + 1}/{MAX_REDIS_RETRIES})")
    except Exception as e:
        print(f"連接過程出錯: {str(e)}")
    retry_count += 1
    sleep(5)  # 增加重試間隔

if not redis_connected:
    print("無法連接到 Redis，將使用同步模式")

# 影片數據結構

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
    print("訪問主頁")
    # 從數據庫獲取所有已上傳的影片
    videos = Video.query.filter(
        Video.status.in_([
            VIDEO_STATUS['UPLOADED'],
            VIDEO_STATUS['COMPRESSED']
        ])
    ).order_by(Video.upload_time.desc()).all()
    
    print(f"找到 {len(videos)} 個影片")
    for video in videos:
        print(f"影片: {video.filename}, 狀態: {video.status}")
        # 檢查文件是否存在
        if not os.path.exists(os.path.join(app.config['UPLOAD_FOLDER'], video.filename)):
            print(f"警告: 文件 {video.filename} 不存在")
    
    # 格式化影片數據
    video_list = [{
        'id': video.id,
        'filename': video.filename,
        'title': video.title or video.filename,  # 如果沒有標題就用文件名
        'votes': video.votes,
        'views': video.views,
        'upload_time': video.upload_time
    } for video in videos]
    
    print(f"返回 {len(video_list)} 個影片數據")
    return render_template('index.html', videos=video_list)

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
        # 先從數據庫中獲取影片記錄
        video = Video.query.filter_by(filename=filename).first()
        if not video:
            return jsonify({'error': '影片記錄不存在'}), 404

        # 嘗試刪除實體文件
        file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        if os.path.exists(file_path):
            os.remove(file_path)
        
        # 如果壓縮文件，也要刪除
        if video.compressed_path and os.path.exists(video.compressed_path):
            os.remove(video.compressed_path)
        
        # 刪除相關的投票記錄
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
    """壓縮影片"""
    try:
        # 確保輸出路徑包含檔名
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
        return jsonify({'error': '沒有影文件'}), 400
    
    file = request.files['video']
    if file.filename == '':
        return jsonify({'error': '沒有選擇件'}), 400
    
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
    file.seek(0)  # 重置文件指針到開始位
    
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
            status=VIDEO_STATUS['UPLOADED'],
            original_path=original_path,
            compressed_path=os.path.join(app.config['UPLOAD_FOLDER'], 'compressed_' + filename)
        )
        db.session.add(video)
        db.session.commit()
        
        # 只記錄上傳狀態，不立即壓縮
        print(f"影片 {filename} 上傳完成，等待系統空閒時進行壓縮")
        
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
        # 先刪除原始文件，再重命名壓縮文件
        if os.path.exists(video.original_path):
            try:
                os.remove(video.original_path)
            except Exception as e:
                app.logger.error(f"刪除原始文件失敗: {str(e)}")
                return
                
        try:
            os.rename(video.compressed_path, video.original_path)
        except Exception as e:
            app.logger.error(f"重命名壓縮文件失敗: {str(e)}")
            return
            
        video.compressed_path = ''
        db.session.commit()
    except Exception as e:
        app.logger.error(f"替換文件失敗: {str(e)}")

@app.route('/vote/<int:video_id>', methods=['POST'])
def vote_video(video_id):
    try:
        video = Video.query.get_or_404(video_id)
        
        # 增加投票數
        video.votes += 1
        db.session.commit()
        
        return jsonify({
            'success': True,
            'votes': video.votes
        })
    except Exception as e:
        db.session.rollback()
        return jsonify({
            'error': str(e)
        }), 500

@app.route('/view/<filename>', methods=['POST'])
def record_view(filename):
    # 從數據庫獲取影片記錄
    video = Video.query.filter_by(filename=filename).first()
    if not video:
        return jsonify({'error': 'Video not found'}), 404
        
    # 增加觀看次數
    video.views += 1
    db.session.commit()
    
    return jsonify({'views': video.views})

@app.route('/uploads/<filename>')
def uploaded_file(filename):
    video = Video.query.filter_by(filename=filename).first()
    if not video:
        return 'File not found', 404
    
    # 標記影片正在使用中
    video.in_use = True
    db.session.commit()
    
    @after_this_request
    def after_request(response):
        # 標記影片不再使用中
        video.in_use = False
        db.session.commit()
        return response
    
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

# 添加新的路由來處理影片播放結束事件
@app.route('/video_ended/<filename>', methods=['POST'])
def video_ended(filename):
    video = Video.query.filter_by(filename=filename).first()
    if not video:
        return jsonify({'error': 'Video not found'}), 404
    
    # 檢查是否可以替換文件
    if (video.status == VIDEO_STATUS['COMPRESSED'] and 
        not video.in_use and 
        video.compressed_path):
        replace_with_compressed(filename)
    
    return jsonify({'success': True})

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
            'mode': 'sync'  # 表示正在使用同步模式
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

@app.route('/rankings')
def get_rankings():
    # 獲取按投票數排序的前 10 個影片
    top_videos = Video.query.order_by(Video.votes.desc()).limit(10).all()
    
    rankings = [{
        'rank': index + 1,
        'id': video.id,
        'filename': video.filename,
        'title': video.title,
        'votes': video.votes,
        'thumbnail': f'/uploads/{video.filename}#t=0.1'  # 用影片第 0.1 秒作為縮圖
    } for index, video in enumerate(top_videos)]
    
    return jsonify(rankings)

# 添加新的路由來檢查和處理待替換���影片
@app.route('/check_pending_compression', methods=['POST'])
def check_pending_compression():
    try:
        # 獲取所有已壓縮尚未替換的影片
        pending_videos = Video.query.filter_by(
            status=VIDEO_STATUS['COMPRESSED'],
            in_use=False
        ).filter(Video.compressed_path != '').all()
        
        for video in pending_videos:
            replace_with_compressed(video.filename)
            
        return jsonify({'success': True, 'processed': len(pending_videos)})
    except Exception as e:
        app.logger.error(f"檢查待替換影片時出錯: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/system_status')
def system_status():
    try:
        from extensions import get_redis, get_queue
        
        redis_conn = get_redis()
        queue = get_queue()
        
        queue_jobs = []
        queue_workers = 0
        ping_success = False
        error_message = None
        
        try:
            if redis_conn:
                ping_success = redis_conn.ping()
                print(f"Redis ping 測試: {ping_success}")
        except Exception as e:
            error_message = f"Redis ping error: {str(e)}"
            print(error_message)
        
        try:
            if queue:
                queue_jobs = queue.get_jobs()
                queue_workers = len(queue.started_job_registry)
                print(f"隊列狀態: 任務數={len(queue_jobs)}, 工作者數={queue_workers}")
        except Exception as e:
            error_message = f"Queue error: {str(e)}"
            print(error_message)

        redis_status = {
            'connected': redis_conn is not None and ping_success,
            'queue_length': len(queue_jobs),
            'workers': queue_workers,
            'ping_success': ping_success,
            'error': error_message
        }
        
        return jsonify({
            'redis_status': redis_status,
            'upload_folder': os.path.exists(app.config['UPLOAD_FOLDER']),
            'instance_folder': os.path.exists('instance'),
            'database': os.path.exists('instance/videos.db')
        })
    except Exception as e:
        print(f"系統狀態檢查錯誤: {str(e)}")
        return jsonify({
            'error': str(e),
            'redis_status': {
                'connected': False,
                'queue_length': 0,
                'workers': 0,
                'ping_success': False
            }
        })

@app.route('/process_compression', methods=['POST'])
def process_compression():
    try:
        if not compression_queue:
            print("壓縮隊列未初始化")
            return jsonify({
                'error': 'Compression queue is not initialized'
            }), 500
            
        # 檢查 worker 狀態
        workers = Worker.all(connection=redis_conn)
        if not workers:
            print("沒有運行中的 worker")
            return jsonify({
                'error': 'No active workers found'
            }), 500
            
        print(f"當前運行的 worker 數量: {len(workers)}")
        
        # 獲取所有待壓縮的影片
        pending_videos = Video.query.filter_by(
            status=VIDEO_STATUS['UPLOADED']
        ).all()
        
        print(f"找到 {len(pending_videos)} 個待壓縮影片")
        
        processed = 0
        for video in pending_videos:
            try:
                # 檢查文件是否存在
                if not os.path.exists(video.original_path):
                    print(f"原始文件不存在: {video.original_path}")
                    continue
                    
                # 入隊前檢查任務是否已存在
                existing_jobs = compression_queue.get_jobs()
                if any(job.args[0] == video.filename for job in existing_jobs):
                    print(f"影片 {video.filename} 已在隊列中")
                    continue
                
                # 使用��列進行異步壓縮
                job = compression_queue.enqueue(
                    'tasks.compress_video_task',
                    video.filename,
                    job_timeout='1h'
                )
                
                if not job:
                    print(f"創建任務失敗: {video.filename}")
                    continue
                    
                print(f"已將影片 {video.filename} 加入壓縮隊列，任務ID: {job.id}")
                
                # 更新影片狀態為壓縮中
                video.status = VIDEO_STATUS['COMPRESSING']
                db.session.commit()
                
                processed += 1
                
            except Exception as e:
                print(f"處理影片 {video.filename} 時出錯: {str(e)}")
                continue
        
        # 獲取隊列狀態
        queue_status = {
            'queued_jobs': len(compression_queue),
            'failed_jobs': len(compression_queue.failed_job_registry),
            'finished_jobs': len(compression_queue.finished_job_registry),
            'started_jobs': len(compression_queue.started_job_registry)
        }
        print(f"隊列狀態: {queue_status}")
        
        return jsonify({
            'success': True,
            'processed': processed,
            'total': len(pending_videos),
            'queue_status': queue_status
        })
        
    except Exception as e:
        print(f"壓縮處理出錯: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/queue_status')
def queue_status():
    try:
        if not compression_queue:
            return jsonify({'error': 'Redis queue not available'})
            
        return jsonify({
            'queued_jobs': len(compression_queue),
            'failed_jobs': len(compression_queue.failed_job_registry),
            'finished_jobs': len(compression_queue.finished_job_registry),
            'started_jobs': len(compression_queue.started_job_registry),
            'deferred_jobs': len(compression_queue.deferred_job_registry)
        })
    except Exception as e:
        return jsonify({'error': str(e)})

@app.route('/check_db')
def check_db():
    try:
        # 檢查數據庫連接
        db.session.execute('SELECT 1')
        
        # 獲取所有影片記錄
        videos = Video.query.all()
        
        # 檢查上傳目錄中的文件
        upload_files = os.listdir(app.config['UPLOAD_FOLDER'])
        
        return jsonify({
            'database_connected': True,
            'video_count': len(videos),
            'videos': [{
                'id': v.id,
                'filename': v.filename,
                'status': v.status,
                'file_exists': os.path.exists(v.original_path)
            } for v in videos],
            'upload_files': upload_files
        })
    except Exception as e:
        return jsonify({
            'error': str(e),
            'database_connected': False
        })

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
