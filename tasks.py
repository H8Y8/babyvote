import psutil
import time
from config import VIDEO_STATUS, MAX_CONCURRENT_TASKS
from extensions import db, compression_queue
from app import app, compress_video
from models import Video

def can_start_compression():
    """檢查系統資源是否允許開始新的壓縮任務"""
    cpu_percent = psutil.cpu_percent(interval=1)
    
    # 如果沒有壓縮隊列，只檢查 CPU 使用率
    if compression_queue is None:
        return cpu_percent < 80
        
    running_tasks = len(compression_queue.started_job_registry)
    return cpu_percent < 80 and running_tasks < MAX_CONCURRENT_TASKS

def compress_video_task(filename):
    """壓縮任務處理函數"""
    with app.app_context():
        video = Video.query.filter_by(filename=filename).first()
        if not video:
            return
        
        try:
            video.status = VIDEO_STATUS['COMPRESSING']
            db.session.commit()
            
            # 等待系統資源可用
            while not can_start_compression():
                time.sleep(5)
            
            if compress_video(video.original_path, video.compressed_path):
                video.status = VIDEO_STATUS['COMPRESSED']
            else:
                video.status = VIDEO_STATUS['UPLOADED']
                
            db.session.commit()
            
        except Exception as e:
            app.logger.error(f"壓縮失敗: {str(e)}")
            video.status = VIDEO_STATUS['UPLOADED']
            db.session.commit() 