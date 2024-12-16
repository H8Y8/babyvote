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
from utils.thumbnail import generate_thumbnail

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
    
    # 獲取所有投票記錄
    votes = Vote.query.order_by(Vote.timestamp.desc()).all()
    
    # 整理投票記錄
    vote_records = []
    for vote in votes:
        video = Video.query.get(vote.video_id)
        if video:
            vote_records.append({
                'id': vote.id,
                'ip': vote.ip_address,
                'video_title': video.title,
                'video_id': video.id,
                'timestamp': vote.timestamp
            })
    
    video_stats = [{
        'filename': video.filename,
        'votes': video.votes,
        'views': video.views,
        'status': video.status,
        'upload_time': video.upload_time
    } for video in videos]
    
    return render_template('admin.html', 
                         videos=video_stats, 
                         STATUS_MAP=STATUS_MAP,
                         vote_records=vote_records)

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
    '-preset', 'medium',         # 壓縮速度（選：ultrafast, superfast, veryfast, faster, fast, medium, slow, slower, veryslow
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
        # 確保輸出檔案有 .mp4 副檔名
        if not output_path.lower().endswith('.mp4'):
            output_path = f"{output_path}.mp4"
            
        # 如果輸出檔案已存在，先刪除
        if os.path.exists(output_path):
            try:
                os.remove(output_path)
                app.logger.info(f"已刪除舊的輸出檔案: {output_path}")
            except Exception as e:
                app.logger.warning(f"刪除舊檔案失敗: {str(e)}")
            
        # 加入 -y 參數以自動覆寫已存在的檔案
        command = [ffmpeg_path, '-y', '-i', input_path] + FFMPEG_PARAMS + [output_path]
        
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
    """自定義的安全檔數，支持中文"""
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
def upload_video():
    try:
        if 'video' not in request.files:
            return jsonify({'success': False, 'error': '沒有上傳檔案'}), 400
            
        # 獲取上傳檔案的原始檔名
        video = request.files['video']
        original_extension = os.path.splitext(video.filename)[1]  # 取得原始副檔名

# 獲取自定義檔名並加上原始副檔名
        custom_filename = request.form.get('custom_filename', '').strip()
        if not custom_filename.endswith(original_extension):
            custom_filename = f"{custom_filename}{original_extension}"
        app.logger.info(f"開始的壓縮影片名稱2: {custom_filename}")
        if not custom_filename:
            return jsonify({'success': False, 'error': '請提供檔案名稱'}), 400
            
        # 確保檔案名稱安全
        original_filename = secure_chinese_filename(f"{custom_filename}")
        app.logger.info(f"改名後的壓縮影片名稱: {original_filename}")
        # 儲存影片
        video_path = os.path.join(app.config['UPLOAD_FOLDER'], original_filename)
        video.save(video_path)
        
        # 生成縮圖
        thumbnail_path = os.path.join(app.config['THUMBNAIL_FOLDER'], f"{original_filename}.jpg")
        if not generate_thumbnail(video_path, thumbnail_path):
            return jsonify({'success': False, 'error': '生成縮圖失敗'}), 500
            
        # 創建影片記錄
        video = Video(
            filename=original_filename,
            title=custom_filename,
            status=VIDEO_STATUS['UPLOADED']
        )
        db.session.add(video)
        db.session.commit()
        
        # 開始壓縮處理
        compress_video_async(original_filename)
        
        return jsonify({
            'success': True,
            'filename': original_filename
        })
        
    except Exception as e:
        if os.path.exists(video_path):
            os.remove(video_path)
        if os.path.exists(thumbnail_path):
            os.remove(thumbnail_path)
        return jsonify({'success': False, 'error': str(e)}), 500

# 確保應用程式啟動時建立必要的目錄
def create_required_directories():
    os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
    os.makedirs(app.config['THUMBNAIL_FOLDER'], exist_ok=True)

# 在應用程式配置中加入縮圖目錄設定
app.config['THUMBNAIL_FOLDER'] = os.path.join('static', 'thumbnails')

# 應用程式啟動時建立目錄
create_required_directories()

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
        
        # 設置壓縮文件路徑，保留原始檔案名稱
        input_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        # 使用原始檔案名稱，只在前面加上 compressed_ 前綴
        compressed_filename = f'compressed_{os.path.splitext(filename)[0]}.mp4'
        output_path = os.path.join(app.config['UPLOAD_FOLDER'], compressed_filename)
        
        app.logger.info(f"開始壓縮影片: {filename}")
        app.logger.info(f"輸入路徑: {input_path}")
        app.logger.info(f"輸出路徑: {output_path}")
        
        # 進行壓縮
        if compress_video(input_path, output_path):
            app.logger.info(f"影片壓縮成功: {filename}")  # 使用原始檔案名稱記錄
            # 直接替換原始文件
            try:
                os.replace(output_path, input_path)
                video.status = VIDEO_STATUS['COMPLETED']
                db.session.commit()
                app.logger.info(f"影片處理完成: {filename}")  # 使用原始檔案名稱記錄
            except Exception as e:
                app.logger.error(f"替換文件失敗: {str(e)}")
                video.status = VIDEO_STATUS['ERROR']
                db.session.commit()
        else:
            app.logger.error(f"影片壓縮失敗: {filename}")  # 使用原始檔案名稱記錄
            video.status = VIDEO_STATUS['ERROR']
            db.session.commit()
            
    except Exception as e:
        app.logger.error(f"壓縮處理過程發生錯誤: {str(e)}")
        if video:
            video.status = VIDEO_STATUS['ERROR']
            db.session.commit()

@app.route('/vote/<filename>', methods=['POST'])
def vote(filename):
    # 使用瀏覽器指紋作為裝置識別
    device_fingerprint = request.headers.get('X-Device-Fingerprint')
    app.logger.info(f"收到投票請求: filename={filename}, fingerprint={device_fingerprint}")
    
    if not device_fingerprint:
        app.logger.error("無法獲取裝置指紋")
        return jsonify({'error': '無法識別裝置'}), 400
        
    video = Video.query.filter_by(filename=filename).first()
    if not video:
        app.logger.error(f"找不到影片: {filename}")
        return jsonify({'error': 'Video not found'}), 404
    
    try:
        app.logger.info(f"檢查是否已投票: video_id={video.id}, fingerprint={device_fingerprint}")
        # 檢查是否已經對這個影片投票
        existing_vote = Vote.query.filter_by(
            video_id=video.id,
            ip_address=device_fingerprint
        ).first()
        
        if existing_vote:
            app.logger.info("取消現有投票")
            # 如果已經投過這個影片，則取消投票
            db.session.delete(existing_vote)
            video.votes -= 1
            voted = False
            previous_vote_id = None
        else:
            app.logger.info("檢查是否投過其他影片")
            # 檢查是否投過其他影片
            previous_vote = Vote.query.filter_by(ip_address=device_fingerprint).first()
            if previous_vote:
                app.logger.info(f"取消之前的投票: video_id={previous_vote.video_id}")
                # 取消之前的投票
                prev_video = Video.query.get(previous_vote.video_id)
                if prev_video:
                    prev_video.votes -= 1
                previous_vote_id = previous_vote.video_id
                db.session.delete(previous_vote)
            else:
                previous_vote_id = None
            
            app.logger.info("新增投票")
            # 新增投票
            new_vote = Vote(video_id=video.id, ip_address=device_fingerprint)
            db.session.add(new_vote)
            video.votes += 1
            voted = True
        
        db.session.commit()
        app.logger.info(f"投票成功: votes={video.votes}, voted={voted}")
        
        return jsonify({
            'votes': video.votes,
            'voted': voted,
            'previousVote': previous_vote_id
        })
        
    except Exception as e:
        db.session.rollback()
        app.logger.error(f"投票失敗: {str(e)}")
        return jsonify({'error': '投票失敗'}), 500

@app.route('/view/<filename>', methods=['POST'])
def record_view(filename):
    try:
        video = Video.query.filter_by(filename=filename).first()
        if not video:
            app.logger.error(f"找不到影片: {filename}")
            return jsonify({'error': 'Video not found'}), 404
            
        # 保留裝置指紋相關代碼，但不使用它來限制觀看次數
        device_fingerprint = request.headers.get('X-Device-Fingerprint')
        app.logger.info(f"收到觀看記錄請求: video_id={video.id}, fingerprint={device_fingerprint}")
        
        # 每次播放都記錄一次觀看
        video.views += 1
        
        # 仍然記錄觀看記錄，但不檢查是否重複
        if device_fingerprint:
            new_view = View(
                video_id=video.id,
                ip_address=device_fingerprint
            )
            db.session.add(new_view)
            
        db.session.commit()
        app.logger.info(f"觀看記錄成功: video_id={video.id}, views={video.views}")
        
        return jsonify({
            'success': True,
            'views': video.views
        })
        
    except Exception as e:
        db.session.rollback()
        app.logger.error(f"記錄觀看次數失敗: {str(e)}")
        return jsonify({'error': '記錄失敗'}), 500

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
    
    # 獲取前用戶的投票
    current_vote = Vote.query.filter_by(ip_address=user_ip).first()
    
    # 獲取所有已完成的影片
    videos = Video.query.filter_by(status='completed').order_by(Video.upload_time).all()
    
    return jsonify([{
        'id': video.id,
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

@app.route('/delete_vote/<int:vote_id>', methods=['POST'])
@requires_auth
def delete_vote(vote_id):
    try:
        vote = Vote.query.get(vote_id)
        if not vote:
            return jsonify({'error': '找不到投票記錄'}), 404
            
        # 更新片的投票數
        video = Video.query.get(vote.video_id)
        if video:
            video.votes -= 1
            
        # 刪除投票記錄
        db.session.delete(vote)
        db.session.commit()
        
        app.logger.info(f"成功刪除投票記錄: vote_id={vote_id}, video_id={vote.video_id}")
        return jsonify({'success': True})
        
    except Exception as e:
        db.session.rollback()
        app.logger.error(f"刪除投票記錄失敗: {str(e)}")
        return jsonify({'error': '刪除失敗'}), 500

# 新增縮圖存取路由
@app.route('/thumbnails/<filename>')
def serve_thumbnail(filename):
    return send_from_directory(app.config['THUMBNAIL_FOLDER'], filename)

# 新增批次生成縮圖的路由
@app.route('/admin/generate_thumbnails', methods=['POST'])
@requires_auth
def generate_missing_thumbnails():
    try:
        # 獲取所有影片
        videos = Video.query.all()
        generated_count = 0
        
        for video in videos:
            # 檢查縮圖是否存在
            thumbnail_path = os.path.join(app.config['THUMBNAIL_FOLDER'], f"{video.filename}.jpg")
            if not os.path.exists(thumbnail_path):
                # 檢查原始影片是否存在
                video_path = os.path.join(app.config['UPLOAD_FOLDER'], video.filename)
                if os.path.exists(video_path):
                    # 生成縮圖
                    if generate_thumbnail(video_path, thumbnail_path):
                        generated_count += 1
                        app.logger.info(f"成功生成縮圖: {video.filename}")
                    else:
                        app.logger.error(f"生成縮圖失敗: {video.filename}")
                else:
                    app.logger.warning(f"找不到原始影片: {video.filename}")
        
        return jsonify({
            'success': True,
            'generated_count': generated_count,
            'message': f'成功生成 {generated_count} 個縮圖'
        })
        
    except Exception as e:
        app.logger.error(f"批次生成縮圖時發生錯誤: {str(e)}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
