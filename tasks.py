import psutil
import time
from rq import Queue
from redis import Redis
from app import app, Video, db, compress_video
from config import VIDEO_STATUS, MAX_CONCURRENT_TASKS

redis_conn = Redis.from_url('redis://redis:6379')
compression_queue = Queue('compression', connection=redis_conn)

def can_start_compression():
    """檢查系統資源是否允許開始新的壓縮任務"""
    cpu_percent = psutil.cpu_percent(interval=1)
    running_tasks = len(compression_queue.started_job_registry)
    
    return cpu_percent < 80 and running_tasks < MAX_CONCURRENT_TASKS

def compress_video_task(filename):
    """壓縮任務處理函數"""
    with app.app_context():
        video = Video.query.filter_by(filename=filename).first()
        if not video:
            return
        
        # 等待系統資源可用
        while not can_start_compression():
            time.sleep(5)
        
        try:
            video.status = VIDEO_STATUS['COMPRESSING']
            db.session.commit()
            
            if compress_video(video.original_path, video.compressed_path):
                video.status = VIDEO_STATUS['COMPRESSED']
            else:
                video.status = VIDEO_STATUS['UPLOADED']
                
            db.session.commit()
            
        except Exception as e:
            app.logger.error(f"壓縮失敗: {str(e)}")
            video.status = VIDEO_STATUS['UPLOADED']
            db.session.commit() 